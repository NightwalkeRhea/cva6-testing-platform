"""Exercise the reuse branch without importing Hammer's optional tool dependencies."""
import ast
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


source = Path(__file__).resolve().parents[1] / "hammer/fsim/vc_zoix/__init__.py"
tree = ast.parse(source.read_text())
helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "sff_has_section")
plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef)
              and node.name == "VC_ZOIX")
fgen = next(node for node in plugin.body if isinstance(node, ast.FunctionDef)
            and node.name == "fgen")
scope = {"os": os, "re": re, "shutil": shutil,
         "HammerVLSILogging": SimpleNamespace(enable_colour=True, enable_tag=True)}
exec(compile(ast.Module(body=[helper, fgen], type_ignores=[]), str(source), "exec"), scope)


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "saf_chiptop0.sff"
        self.settings = {"fsim.inputs.campaign_simv_daidir": "simv.daidir",
                         "fsim.vc_zoix.reuse_generated_faults": True}
        self.tool = SimpleNamespace(run_dir=str(self.root), fault_model="saf",
                                    campaign_tb_dut="TestDriver.chiptop0", logger=Mock(),
                                    get_setting=self.settings.__getitem__, run_executable=Mock())

    def test_reuse_preserves_list_and_never_invokes_fcc(self):
        content = '# FaultGenerate is mentioned only in a comment\nFaultList {\n NA 0 {PORT "core.a[0]"}\n}\n'
        self.output.write_text(content)
        self.assertTrue(scope["fgen"](self.tool))
        self.tool.run_executable.assert_not_called()
        self.assertEqual(self.output.read_text(), content)

    def test_reuse_rejects_missing_empty_or_recipe_files(self):
        for content in (None, "", "FaultList {}", "FaultGenerate { NA [0,1] {PORT \"core.*\"} }",
                        'FaultList { NA 0 {PORT "core.a"} }\nFaultGenerate {}'):
            with self.subTest(content=content):
                self.output.unlink(missing_ok=True)
                if content is not None:
                    self.output.write_text(content)
                self.assertFalse(scope["fgen"](self.tool))
        self.tool.run_executable.assert_not_called()

    def test_comments_and_strings_are_not_directives(self):
        detect = scope["sff_has_section"]
        self.assertFalse(detect('# FaultGenerate {\n/* FaultGenerate {} */\n"FaultGenerate {}"', "FaultGenerate"))
        self.assertTrue(detect('FaultGenerate\n{ NA [0,1] {PORT "core.*"} }', "FaultGenerate"))

    def test_default_still_generates_faults(self):
        compiler = self.root / "vc_fcc"
        compiler.touch()
        self.settings.update({"fsim.vc_zoix.reuse_generated_faults": False,
                              "fsim.vc_zoix.vc_fcc_bin": str(compiler)})
        self.tool.fsim_generate_faults = True
        self.tool.standard_fault_format = "gen_saf_chiptop0.sff"
        self.tool.run_executable.side_effect = lambda *args, **kwargs: self.output.write_text("generated")
        self.assertTrue(scope["fgen"](self.tool))
        self.tool.run_executable.assert_called_once()


if __name__ == "__main__":
    unittest.main()
