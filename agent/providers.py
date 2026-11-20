
from __future__ import annotations
import json
import re
import threading
import time
import requests
from .config import as_int, LiveConfig
from . import db

class AIError(RuntimeError):
    pass

def parse_json(text):
    """Require a real JSON object; tolerate fenced JSON but never execute generated code."""
    text=text.strip()
    if text.startswith('```'):
        text=re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text=re.sub(r'\s*```$', '', text)
    try:
        value=json.loads(text)
    except json.JSONDecodeError:
        start=text.find('{'); end=text.rfind('}')
        if start<0 or end<=start:
            raise AIError('Model did not return JSON')
        value=json.loads(text[start:end+1])
    if not isinstance(value,dict):
        raise AIError('Expected JSON object')
    return value

class AIRouter:
    def __init__(self, live: LiveConfig):
        self.live=live
        self.lock=threading.Lock()
        self.pending={}
        self.cooldown={}

    def configured(self, env):
        order=[x.strip() for x in env.get('AI_PROVIDER_ORDER','gemini,groq,cloudflare,ollama,openrouter').split(',')]
        output=[]
        for name in order:
            enabled=env.get(f'{name.upper()}_ENABLED','1' if name in ('gemini','groq') else '0')=='1'
            key=env.get({'gemini':'GEMINI_API_KEY','groq':'GROQ_API_KEY','cloudflare':'CLOUDFLARE_API_KEY',
                         'ollama':'OLLAMA_BASE_URL','openrouter':'OPENROUTER_API_KEY'}.get(name,''),'')
            if name in ('gemini','groq','cloudflare','ollama','openrouter') and enabled and key:
                if name=='gemini':
                    primary=env.get('GEMINI_MODEL') or env.get('GEMINI_PRIMARY_MODEL','gemini-2.5-flash')
                    fallback=env.get('GEMINI_FALLBACK_MODELS') or env.get('GEMINI_FALLBACK_MODEL','')
                    models=[primary]+[m.strip() for m in fallback.split(',') if m.strip()]
                else:
                    models=[env.get(f'{name.upper()}_MODEL','')]

                disabled={m.strip() for m in env.get('GEMINI_DISABLED_MODELS','').split(',') if m.strip()}
                for model in models:
                    if model and model not in disabled and (name,model) not in output:
                        output.append((name,model))
        return output

    def _admit(self, provider, text_size, env, model=None):
        """Conservative local quota accounting. Provider-side quotas may be shared outside app."""
        in_est=max(1,text_size//4)
        margin=float(env.get(f'{provider.upper()}_RATE_SAFETY','0.9') or 0.9)
        cooldown_key=f'{provider}/{model}' if model else provider
        with self.lock:
            if self.cooldown.get(cooldown_key,0)>time.time():
                return False
            minute=db.limit_totals(provider,60)
            day=db.limit_totals(provider,86400)
            active=self.pending.get(provider,0)
            for field, current, limit_name in [
              ('requests',minute['requests']+active,'RPM'),
              ('requests',day['requests']+active,'RPD'),
              ('total_tokens',minute['input_tokens']+minute['output_tokens']+active*in_est+in_est,'TPM'),
              ('total_tokens',day['input_tokens']+day['output_tokens']+active*in_est+in_est,'TPD')]:
                maximum=as_int(env.get(f'{provider.upper()}_{limit_name}'))
                if maximum>0 and current >= maximum*margin:
                    return False
            if provider == 'cloudflare':
                neurons_limit=as_int(env.get('CLOUDFLARE_DAILY_NEURONS'))
                input_cost=float(env.get('CLOUDFLARE_INPUT_NEURONS_PER_M','0') or 0)
                output_cost=float(env.get('CLOUDFLARE_OUTPUT_NEURONS_PER_M','0') or 0)
                estimated_neurons=(day['input_tokens']*input_cost+day['output_tokens']*output_cost)/1_000_000
                forecast_inflight=(active+1)*in_est*input_cost/1_000_000
                safety=float(env.get('CLOUDFLARE_NEURON_SAFETY','0.9') or 0.9)
                if neurons_limit>0 and estimated_neurons+forecast_inflight>=neurons_limit*safety:
                    return False
            self.pending[provider]=active+1
            return True

    def _release(self, provider):
        with self.lock:
            self.pending[provider]=max(0,self.pending.get(provider,1)-1)

    def _call(self,provider,model,prompt,env,max_output):
        if provider=='gemini':
            url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
            r=requests.post(url, headers={'x-goog-api-key':env['GEMINI_API_KEY']}, json={
                'contents':[{'role':'user','parts':[{'text':prompt}]}],
                'generationConfig':{'temperature':0,'maxOutputTokens':max_output}}, timeout=150)
            if not r.ok:
                raise AIError(f'HTTP {r.status_code}')
            obj=r.json()
            parts=obj.get('candidates',[{}])[0].get('content',{}).get('parts',[])
            response='\n'.join(x.get('text','') for x in parts)
            usage=obj.get('usageMetadata',{})
            return response,usage.get('promptTokenCount'),usage.get('candidatesTokenCount')
        base_url={'groq':env.get('GROQ_BASE_URL','https://api.groq.com/openai/v1'),
                  'cloudflare':env.get('CLOUDFLARE_BASE_URL','https://api.cloudflare.com/client/v4/accounts/'+env.get('CLOUDFLARE_ACCOUNT_ID','')+'/ai/v1'),
                  'ollama':env.get('OLLAMA_BASE_URL','http://localhost:11434/v1'),
                  'openrouter':env.get('OPENROUTER_BASE_URL','https://openrouter.ai/api/v1')}[provider]
        headers={'Content-Type':'application/json'}
        key=env.get(f'{provider.upper()}_API_KEY','')
        if provider != 'ollama':
            headers['Authorization']=f'Bearer {key}'
        url=base_url.rstrip('/')+'/chat/completions'
        r=requests.post(url,headers=headers,json={
            'model':model,'messages':[{'role':'user','content':prompt}],
            'temperature':0,'max_tokens':max_output,'stream':False},
            timeout=min(300,max(30,as_int(env.get(f'{provider.upper()}_READ_TIMEOUT'),180))))
        if not r.ok:
            raise AIError(f'HTTP {r.status_code}')
        obj=r.json()
        output=obj['choices'][0]['message']['content']
        if isinstance(output,list):
            output='\n'.join(chunk.get('text','') for chunk in output if isinstance(chunk,dict))
        usage=obj.get('usage') or {}
        return str(output),usage.get('prompt_tokens'),usage.get('completion_tokens')

    def generate(self,prompt,task='analysis'):
        errors=[]
        env=self.live.snapshot()  # Snapshot at dispatch; next call picks up updated file.
        candidates=self.configured(env)
        if not candidates:
            raise AIError('No AI provider enabled with credentials in api_keys.env')
        max_output=as_int(env.get('AGENT_MAX_OUTPUT_TOKENS'),4096)
        max_output=max(512,min(max_output,8192)) if task=='fix' else min(1200,max_output)
        for provider,model in candidates:
            if not self._admit(provider,len(prompt),env,model if provider=='gemini' else None):
                errors.append(f'{provider}/{model}: locally rate-limited/cooling down')
                continue
            try:
                output,in_actual,out_actual=self._call(provider,model,prompt,env,max_output)
                in_n=as_int(in_actual,max(1,len(prompt)//4))
                out_n=as_int(out_actual,max(1,len(output)//4))
                db.usage(provider,model,in_n,out_n,True)
                return parse_json(output), f'{provider}/{model}'
            except (AIError,requests.RequestException,KeyError,ValueError,IndexError,json.JSONDecodeError) as e:
                message=str(e)
                status=re.search(r'HTTP (\d{3})',message)
                code=status.group(1) if status else type(e).__name__
                db.usage(provider,model,max(1,len(prompt)//4),0,False,code)
                errors.append(f'{provider}/{model}: {code}')
                if code=='429':
                    with self.lock:
                        cooldown_key=f'{provider}/{model}' if provider=='gemini' else provider
                        self.cooldown[cooldown_key]=time.time()+90
            finally:
                self._release(provider)
        raise AIError('All AI providers unavailable: '+', '.join(errors))
