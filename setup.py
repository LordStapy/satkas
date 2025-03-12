
import setuptools
from satkas import __name__, __version__

with open('README.md', 'r') as readme:
    long_description = readme.read()

with open('requirements.txt', 'r') as requirements:
    dependencies = requirements.read()

setuptools.setup(
    name=__name__,
    version=__version__,
    author='LordStapy',
    description='Bitcoin-Kaspa Atomic Swap',
    long_description=long_description,
    long_description_content_type='text/markdown',
    packages=setuptools.find_packages(),
    license='WTF',
    install_requires=dependencies,
    url='https://github.com/LordStapy/satkas',
    classifiers=['Programming Language :: Python :: 3'],
    python_requires='>=3.10'
)
