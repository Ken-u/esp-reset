import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_ci',ROOT/'tools/release_ci.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_only_changed_main_version_can_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'version.txt').write_text('0.5.0\n')
            subprocess.run(['git','add','.'],cwd=root,check=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.com','commit','-qm','base'],cwd=root,check=True)
            before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            event={'before':before}
            self.assertEqual(release.plan(root,'push','refs/heads/main',event)['publish'],'false')
            (root/'version.txt').write_text('0.5.1\n')
            self.assertEqual(release.plan(root,'push','refs/heads/main',event)['publish'],'true')
            self.assertEqual(release.plan(root,'push','refs/heads/feature',event)['publish'],'false')
            self.assertEqual(release.plan(root,'pull_request','refs/pull/3/merge',{'pull_request':{'base':{'sha':before}}})['publish'],'false')
            self.assertEqual(release.plan(root,'workflow_dispatch','refs/heads/main',{})['publish'],'false')
            (root/'version.txt').write_text('0.4.9\n')
            with self.assertRaises(ValueError):release.plan(root,'push','refs/heads/main',event)
            (root/'version.txt').write_text('v0.5.1\n')
            with self.assertRaises(ValueError):release.read_version(root)

    def test_first_version_file_migrates_from_legacy_cmake(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);subprocess.run(['git','init','-q',str(root)],check=True)
            (root/'CMakeLists.txt').write_text('project(esp-reset VERSION 0.4.0)\n')
            subprocess.run(['git','add','.'],cwd=root,check=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.com','commit','-qm','legacy'],cwd=root,check=True)
            before=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            (root/'version.txt').write_text('0.5.0\n')
            self.assertEqual(release.previous_version(root,before),'0.4.0')
            self.assertEqual(release.plan(root,'push','refs/heads/main',{'before':before})['publish'],'true')

    def test_release_archives_preserve_one_executable_entry_and_require_both_boards(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle';bundle.mkdir()
            for d in ('firmware','tools','docs'):(bundle/d).mkdir()
            (bundle/'espreset').write_text('#!/bin/sh\n');(bundle/'espreset').chmod(0o755)
            for name in ('README.txt','SHA256SUMS'):(bundle/name).write_text('test')
            (bundle/'version.txt').write_text('0.5.0\n');(bundle/'firmware/esp-reset.bin').write_bytes(b'firmware')
            output=root/'assets'
            release.pack(bundle,output,release.BOARDS[0],'0.5.0')
            with self.assertRaises(ValueError):release.checksums(output,'0.5.0')
            release.pack(bundle,output,release.BOARDS[1],'0.5.0');release.checksums(output,'0.5.0')
            self.assertEqual(len(list(output.iterdir())),5)
            archive=next(output.glob('*.zip'))
            with zipfile.ZipFile(archive) as stream:
                entry=next(info for info in stream.infolist() if info.filename.endswith('/espreset'))
                self.assertEqual((entry.external_attr>>16)&0o111,0o111)
            (bundle/'reset.sh').write_text('legacy')
            with self.assertRaises(ValueError):release.pack(bundle,output,release.BOARDS[0],'0.5.0')
