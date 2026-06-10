"""Clean up test files: ensure unittest import present, fix class declarations."""

import re, glob

for fpath in glob.glob('tests/test_*.py') + ['tests/conftest.py']:
    with open(fpath) as f:
        lines = f.readlines()

    content = ''.join(lines)
    original = content

    # Ensure 'import unittest' is present
    if 'import unittest' not in content:
        # Add after the docstring or first import
        lines.insert(0, 'import unittest\n')
        content = ''.join(lines)

    # Fix class declarations: class TestXxx: -> class TestXxx(unittest.TestCase):
    content = re.sub(
        r'^class (Test\w+):\s*$',
        r'class \1(unittest.TestCase):',
        content,
        flags=re.MULTILINE
    )

    # Remove function parameter fixtures (pytest fixture injection)
    # e.g., def test_xxx(self, sample_persona_ids): -> def test_xxx(self):
    content = re.sub(
        r'(def test_\w+\(self)\)[^)]*\):',
        r'\1):',
        content
    )

    # Remove any remaining pytest imports
    content = re.sub(r'^from pytest.*', '', content, flags=re.MULTILINE)
    content = re.sub(r'^import pytest.*', '', content, flags=re.MULTILINE)

    # Fix pytest.skip references
    content = content.replace('pytest.skip(', 'self.skipTest(')
    content = re.sub(r'pytest\.skip\b', 'self.skipTest("skipped")', content)

    if content != original:
        with open(fpath, 'w') as f:
            f.write(content)
        print(f"Fixed: {fpath}")
    else:
        print(f"OK: {fpath}")

print("\nAll files cleaned.")
