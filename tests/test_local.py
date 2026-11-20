import json
from pathlib import Path
import pytest
from agent.providers import parse_json,AIError
from agent.workflow import apply_changes,WorkflowError,safe_path
from agent.config import LiveConfig

def test_json_fenced():
    assert parse_json('```json\n{"score": 90}\n```')['score']==90

def test_json_requires_object():
    with pytest.raises(AIError): parse_json('[1,2]')

def test_patching_exact_and_python_syntax(tmp_path):
    path=tmp_path/'example.py'
    path.write_text('x = 1\n',encoding='utf-8')
    assert apply_changes(tmp_path,[{'path':'example.py','search':'x = 1','replace':'x = 2'}])==['example.py']
    assert path.read_text()=='x = 2\n'

def test_unsafe_paths_blocked(tmp_path):
    with pytest.raises(WorkflowError): safe_path(tmp_path,'../secrets.txt')
    with pytest.raises(WorkflowError): safe_path(tmp_path,'.github/workflows/ci.yml')
    with pytest.raises(WorkflowError): safe_path(tmp_path,'.env')

def test_atomic_validation_of_multiple_edits(tmp_path):
    path=tmp_path/'test.py';path.write_text('a=1\n')
    with pytest.raises(WorkflowError):
        apply_changes(tmp_path,[{'path':'test.py','search':'a=1','replace':'a=2'},
                               {'path':'missing.py','search':'a','replace':'b'}])
    assert path.read_text()=='a=1\n'

def test_live_env_reload(tmp_path):
    path=tmp_path/'api_keys.env';path.write_text('GROQ_API_KEY=first\n')
    cfg=LiveConfig(path)
    assert cfg.snapshot()['GROQ_API_KEY']=='first'
    path.write_text('GROQ_API_KEY=second-key-value\n')
    assert cfg.snapshot()['GROQ_API_KEY']=='second-key-value'


def test_security_sensitive_paths_blocked(tmp_path):
    for name in ('authentik/stages/x.py', 'src/auth/login.js', 'app/permissions.py', 'lib/crypto/aes.go', 'api/oauth_client.ts'):
        with pytest.raises(WorkflowError): safe_path(tmp_path, name)
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'avatar_service.js').write_text('x')
    assert safe_path(tmp_path, 'src/avatar_service.js')
