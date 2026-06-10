"""Clean up pytest references from test files."""

import re, glob

# Fix conftest.py
with open('tests/conftest.py') as f:
    content = f.read()

content = content.replace('import pytest', 'import unittest')
content = content.replace('@pytest.fixture(autouse=True)', '')
content = content.replace('@pytest.fixture', '')
content = content.replace('autouse=True', '')

with open('tests/conftest.py', 'w') as f:
    f.write(content)

# Fix remaining pytest patterns in all test files
for fpath in glob.glob('tests/test_*.py') + ['tests/conftest.py']:
    with open(fpath) as f:
        content = f.read()

    # Remove import pytest if it exists
    content = re.sub(r'^import pytest.*$', '', content, flags=re.MULTILINE)
    content = re.sub(r'^from pytest.*$', '', content, flags=re.MULTILINE)

    # Fix class declarations that might be missing TestCase
    content = re.sub(
        r'^class (Test\w+):',
        r'class \1(unittest.TestCase):',
        content,
        flags=re.MULTILINE
    )

    with open(fpath, 'w') as f:
        f.write(content)

print("Done cleaning pytest references")
