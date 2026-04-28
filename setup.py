from setuptools import setup, find_packages
import codecs
import os

here = os.path.abspath(os.path.dirname(__file__))

with codecs.open(os.path.join(here, "README.md"), encoding="utf-8") as fh:
    long_description = "\n" + fh.read()

VERSION = "1.0.0"
DESCRIPTION = "Non-intrusive monitoring and control support for AlLoRa-based deployments"

setup(
    name="non-intrusive-monitoring-for-allora",
    version=VERSION,
    author="Diego Rios Gomez",
    author_email="driogom@upv.edu.es",
    description=DESCRIPTION,
    long_description_content_type="text/markdown",
    long_description=long_description,
    packages=find_packages(),
    install_requires=[],
    keywords=[
        "IoT",
        "LoRa",
        "AlLoRa",
        "Environmental Intelligence",
        "LPWAN",
        "MicroPython",
        "Arduino",
        "LilyGO",
        "Raspberry Pi",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: MicroPython",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: Microsoft :: Windows",
        "Topic :: Software Development :: Embedded Systems",
        "Topic :: Scientific/Engineering",
    ],
)