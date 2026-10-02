import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

spec=importlib.util.spec_from_file_location('mirror_settings',Path(__file__).with_name('settings.py'))
settings=importlib.util.module_from_spec(spec); spec.loader.exec_module(settings)

class SettingsMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        (self.home/'.claude').mkdir(); (self.home/'.codex').mkdir()
        (self.home/'.claude/settings.json').write_text(json.dumps({'skillOverrides':{'cloudflare':'off'}}))
        (self.home/'.claude.json').write_text(json.dumps({'mcpServers':{'playwright':{'command':'npx','args':['-y','@playwright/mcp@latest']}}}))
        skill=self.home/'.codex/skills/cloudflare'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
        self.config=self.home/'.codex/config.toml'
        self.original=b'model = "existing-model"\nsandbox_mode = "workspace-write"\napprovals_reviewer = "auto_review"\n'
        self.config.write_bytes(self.original)
    def test_plan_leaves_everything_untouched(self):
        settings.apply(self.home)
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertFalse((self.home/'.codex/AGENTS.md').exists())
    def test_apply_preserves_policy_and_backup_and_is_idempotent(self):
        result=settings.apply(self.home,False)
        self.assertEqual((Path(result['backup'])/'config.toml').read_bytes(),self.original)
        parsed=tomllib.loads(self.config.read_text())
        for key,value in tomllib.loads(self.original.decode()).items(): self.assertEqual(parsed[key],value)
        self.assertFalse(parsed['skills']['config'][0]['enabled'])
        before=self.config.read_bytes()
        second=settings.apply(self.home,False)
        self.assertEqual(before,self.config.read_bytes()); self.assertIsNone(second['backup'])
    def test_existing_custom_connection_and_guidance_survive(self):
        self.config.write_text(self.original.decode()+'\n[mcp_servers.notion]\nurl="https://existing.example/mcp"\n')
        guide=self.home/'.codex/AGENTS.md'; guide.write_text('Existing owner guidance')
        settings.apply(self.home,False)
        self.assertEqual(tomllib.loads(self.config.read_text())['mcp_servers']['notion']['url'],'https://existing.example/mcp')
        self.assertEqual(guide.read_text(),'Existing owner guidance')
    def test_conflicting_skill_override_is_not_silently_changed(self):
        path=self.home/'.codex/skills/cloudflare/SKILL.md'
        self.config.write_text(self.original.decode()+'\n[[skills.config]]\npath='+json.dumps(str(path))+'\nenabled=true\n')
        before=self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'Conflicting'):settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),before)
    def test_edited_managed_settings_are_preserved(self):
        settings.apply(self.home,False)
        self.config.write_text(self.config.read_text().replace('https://mcp.notion.com/mcp','https://custom.example/mcp'))
        before=self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'were edited'):settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),before)
    def test_missing_report_cannot_disable_edit_protection(self):
        settings.apply(self.home,False)
        (self.home/'.codex/mirrors/claude/settings-report.json').unlink()
        self.config.write_text(self.config.read_text().replace('https://mcp.notion.com/mcp','https://custom.example/mcp'))
        before=self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'ownership evidence'):settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),before)
    def test_missing_block_hash_is_not_adopted(self):
        settings.apply(self.home,False)
        report=self.home/'.codex/mirrors/claude/settings-report.json'
        report.write_text('{}')
        before=self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'ownership evidence'):settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),before)
    def test_concurrent_edit_is_preserved_even_on_unchanged_apply(self):
        from unittest.mock import patch
        settings.apply(self.home,False)
        original_make=settings.make_config
        edited=self.config.read_bytes()+b'\n# owner edit\n'
        def edit_after_read(original,home):
            result=original_make(original,home)
            self.config.write_bytes(edited)
            return result
        with patch.object(settings,'make_config',side_effect=edit_after_read):
            with self.assertRaisesRegex(RuntimeError,'Config changed'):settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),edited)
    def test_settings_lock_times_out_and_releases_after_failure(self):
        import fcntl
        from unittest.mock import patch
        lock=self.home/'.codex/mirrors/claude/settings.lock'
        lock.parent.mkdir(parents=True)
        with lock.open('a') as holder:
            fcntl.flock(holder,fcntl.LOCK_EX)
            with patch.object(settings,'LOCK_TIMEOUT_SECONDS',.05):
                with self.assertRaisesRegex(TimeoutError,'retry after'):
                    settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),self.original)
        settings.apply(self.home,False)
    def test_missing_source_is_clear_and_preserves_config(self):
        (self.home/'.claude/settings.json').unlink()
        with self.assertRaisesRegex(ValueError,'restore the source'):
            settings.apply(self.home,False)
        self.assertEqual(self.config.read_bytes(),self.original)
    def test_malformed_config_is_not_overwritten(self):
        self.config.write_text('invalid = [')
        with self.assertRaises(tomllib.TOMLDecodeError):settings.apply(self.home,False)
        self.assertEqual(self.config.read_text(),'invalid = [')


# The detector and settings are the two configuration-side mirror entrypoints.
class AppServerTransportTests(unittest.TestCase):
    def call_server(self,script,timeout=1):
        script=script.replace("for _ in range(3):sys.stdin.readline()", 'sys.stdin.readline()\nos.write(1, b\'{"id":0,"result":{}}\\n\')\nfor _ in range(2):sys.stdin.readline()')
        import subprocess
        import sys
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('mirror_rpc',Path(__file__).with_name('mirror.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        popen=subprocess.Popen
        children=[]
        def launch(*args,**kwargs):
            child=popen([sys.executable,'-c',script],**kwargs)
            children.append(child)
            return child
        try:
            with patch.object(module.subprocess,'Popen',side_effect=launch):
                return module.rpc('test',{},timeout=timeout)
        finally:
            for child in children:
                self.assertIsNotNone(child.poll())
                self.assertTrue(child.stdout.closed)
                self.assertTrue(child.stderr.closed)
    def test_missing_codex_produces_controlled_error(self):
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('mirror_missing_rpc',Path(__file__).with_name('mirror.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module.subprocess,'Popen',side_effect=FileNotFoundError('missing codex')):
            with self.assertRaisesRegex(module.AppServerError,'Check that codex is installed'):
                module.rpc('test',{})
    def test_request_waits_for_initialize_response(self):
        script='import os,sys\nfirst=os.read(0,65536)\nassert first.count(b"\\n")==1, first\nos.write(1,b\'{"id":0,"result":{}}\\n\')\nfor _ in range(2):sys.stdin.readline()\nos.write(1,b\'{"id":1,"result":{"ok":true}}\\n\')'
        self.assertEqual(self.call_server(script),{'ok':True})
    def test_batched_notifications_do_not_hide_the_response(self):
        script='import os,sys,time\nfor _ in range(3):sys.stdin.readline()\nos.write(1,b\''+b'{"id":0,"result":{}}\n{"method":"notice"}\n{"id":1,"result":{"ok":true}}\n'.decode().replace('\n','\\n')+'\')\ntime.sleep(5)'
        self.assertEqual(self.call_server(script),{'ok':True})
    def test_large_stderr_is_drained_without_blocking_stdout(self):
        script='import os,sys\nfor _ in range(3):sys.stdin.readline()\nos.write(2,b"diagnostic "*20000)\nos.write(1,b\'{"id":1,"result":{"ok":true}}\\n\')'
        self.assertEqual(self.call_server(script),{'ok':True})
    def test_partial_stdout_times_out_and_child_is_reaped(self):
        script='import os,sys,time\nfor _ in range(3):sys.stdin.readline()\nos.write(2,b"specific startup failure")\nos.write(1,b\'{"id":\')\ntime.sleep(5)'
        with self.assertRaisesRegex(RuntimeError,'specific startup failure'):
            self.call_server(script,timeout=.2)

if __name__=='__main__': unittest.main()
