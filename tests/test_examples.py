from pathlib import Path

import pytest

from solarpv.project import Project, compute

EXAMPLES = sorted((Path(__file__).parent.parent / "examples" / "projects").glob("*.spvd"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_projects_open_and_design(path, library, rules):
    result = compute(Project.load(path), library, rules)
    assert result.design is not None, result.design_error
    assert not [c for c in result.checks if c.status == "fail"]
