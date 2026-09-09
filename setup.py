
import ast
import os
from setuptools import find_packages, setup
from pathlib import Path


def read_pkg_meta():
    """Read package metadata from satkas/__init__.py without importing it."""
    init_py = Path(__file__).resolve().parent / "satkas" / "__init__.py"
    tree = ast.parse(init_py.read_text())
    wanted = {
        "__name__",
        "__version__",
        "__author__",
        "__license__",
        "__url__",
        "__description__",
        "__keywords__",
        "__classifiers__",
    }
    meta = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        meta[target.id] = ast.literal_eval(node.value)
    missing = wanted - meta.keys()
    if missing:
        raise RuntimeError(f"Missing metadata in satkas/__init__.py: {sorted(missing)}")
    return meta


meta = read_pkg_meta()


def find_kv_files():
    """Dynamically discover all packages containing .kv files."""
    packages = find_packages()
    package_data = {}
    # Base package directory
    base_dir = Path("satkas")
    # Walk through all directories and find .kv files
    for root, dirs, files in os.walk(base_dir):
        # Check if this directory contains .kv files
        patterns = [f"*{Path(f).suffix}" for f in files if Path(f).suffix in {'.kv', '.png'}]
        if patterns:
            # Convert directory path to package name
            rel_path = Path(root).relative_to(Path('.'))
            package_name = str(rel_path).replace(os.sep, '.')
            # Check if this directory path corresponds to a valid package
            # (either it's in packages, or it's a subdirectory of a package)
            path_parts = package_name.split('.')
            # Check if any package in the path exists
            for i in range(len(path_parts), 0, -1):
                check_package = '.'.join(path_parts[:i])
                if check_package in packages:
                    # This is a valid package or subdirectory of a package
                    package_data[package_name] = sorted(set(patterns))
                    break
    # Add config files (not really used, but included for possible future use)
    package_data["satkas.config"] = ["*.yaml", "*.json", "*.ini"]
    return package_data


with open('README.md', 'r') as readme:
    long_description = readme.read()

with open('requirements.txt', 'r') as requirements:
    dependencies = requirements.read()

setup(
    name=meta["__name__"],
    version=meta["__version__"],
    author=meta["__author__"],
    url=meta["__url__"],
    description=meta["__description__"],
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    # Include non-Python files
    include_package_data=True,
    package_data=find_kv_files(),
    entry_points={"console_scripts": ["satkas=satkas.cli:main"]},
    license=meta["__license__"],
    install_requires=dependencies,
    extras_require={
        'kivymd': ['kivymd==2.0.0'],
    },
    keywords=meta["__keywords__"],
    classifiers=meta["__classifiers__"],
    python_requires='>=3.10'
)
