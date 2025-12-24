
import os
from setuptools import find_packages, setup
from pathlib import Path
from satkas import (
    __name__,
    __version__,
    __author__,
    __license__,
    __url__,
    __description__,
    __keywords__,
    __classifiers__
)


def find_kv_files():
    """Dynamically discover all packages containing .kv files."""
    packages = find_packages()
    package_data = {}
    # Base package directory
    base_dir = Path("satkas")
    # Walk through all directories and find .kv files
    for root, dirs, files in os.walk(base_dir):
        # Check if this directory contains .kv files
        if any(f.endswith('.kv') for f in files):
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
                    package_data[package_name] = ["*.kv"]
                    break
    # Add config files (not really used, but included for possible future use)
    package_data["satkas.config"] = ["*.yaml", "*.json", "*.ini"]
    return package_data


with open('README.md', 'r') as readme:
    long_description = readme.read()

with open('requirements.txt', 'r') as requirements:
    dependencies = requirements.read()

setup(
    name=__name__,
    version=__version__,
    author=__author__,
    url=__url__,
    description=__description__,
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    # Include non-Python files
    include_package_data=True,
    package_data=find_kv_files(),
    entry_points={"console_scripts": ["satkas=satkas.cli:main"]},
    license=__license__,
    install_requires=dependencies,
    keywords=__keywords__,
    classifiers=__classifiers__,
    python_requires='>=3.10'
)
