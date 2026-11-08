"""为任意已登录账号准备公开贡献证据；缺失历史明确进入冷启动。"""
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from contextlib import closing


def account_evidence(request,config,collector=None):
    from run_gated_feature_worker import atomic_json
    login=(request.get('developer_key') or '').casefold()
    if not login:
        key=request['context_key']
        if not re.fullmatch(r'[0-9a-f]{64}',key):raise ValueError('Invalid anonymous context')
        directory=Path(config['requests']).parent/'anonymous_evidence'/key
        manifest=directory/'evidence_manifest.json'
        now=datetime.now(timezone.utc)
        if manifest.is_file():
            previous=json.loads(manifest.read_text(encoding='utf-8'))
            if now-datetime.fromisoformat(previous['as_of'])<timedelta(hours=24):return directory
        atomic_json(manifest,dict(login='anonymous',github_numeric_id=None,as_of=now.isoformat(),
            retained_X=[],documents=[],responses=[],history_complete=False,
            collection_status='not_requested',scope='anonymous_profile_only'))
        return directory
    if not re.fullmatch(r'[a-z0-9-]{1,39}',login):raise ValueError('Authenticated GitHub login required')
    database=Path(config['database']).resolve()
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as connection:
        row=connection.execute('SELECT github_user_id FROM oss_user WHERE github_login=? COLLATE NOCASE AND deleted_at IS NULL',(login,)).fetchone()
    if row is None:raise ValueError('Account is not an active platform user')
    uid=int(row[0]);root=Path(config.get('evidence_root',str(Path(config['requests']).parent/'account_evidence')))
    account=root/str(uid);pointer=account/'current.json'
    now=datetime.now(timezone.utc)
    if pointer.is_file():
        name=json.loads(pointer.read_text())['generation']
        if not re.fullmatch(r'[0-9a-f]{32}',name):raise ValueError('Invalid evidence pointer')
        directory=account/name
        evidence=json.loads((directory/'evidence_manifest.json').read_text(encoding='utf-8'))
        observed=datetime.fromisoformat(evidence['as_of'])
        ttl=timedelta(minutes=5) if evidence.get('collection_status')=='unavailable' else timedelta(hours=24)
        if evidence['github_numeric_id']==uid and evidence['login'].casefold()==login and observed<=now<observed+ttl:return directory
    directory=account/uuid.uuid4().hex
    if collector is None:
        from collect_gated_user_evidence import collect
        from oss_mentor.services.github_profile_source import GitHubProfileSource
        source=GitHubProfileSource()
        collector=lambda login,directory,uid:collect(login,directory,get_json=source._get,expected_id=uid)
    try:
        collector(login,directory,uid)
        evidence=json.loads((directory/'evidence_manifest.json').read_text(encoding='utf-8'))
        if evidence['github_numeric_id']!=uid or evidence['login'].casefold()!=login:raise ValueError('Collected identity mismatch')
    except ValueError:
        raise  # Identity/content corruption must not be disguised as a network outage.
    except Exception:
        directory=account/uuid.uuid4().hex
        atomic_json(directory/'evidence_manifest.json',dict(login=login,github_numeric_id=uid,
            as_of=now.isoformat(),retained_X=[],documents=[],responses=[],history_complete=False,
            collection_status='unavailable',scope='authenticated_profile_only',
            limitations=['Public history unavailable; absence of evidence is not absence of contributions.']))
    atomic_json(pointer,dict(generation=directory.name))
    return directory
