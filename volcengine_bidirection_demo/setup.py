from setuptools import find_packages, setup

setup(
    name="volc-speech-python-sdk",
    version="0.1.0",
    packages=find_packages(include=["protocols"]),
    install_requires=[
        "websockets>=14.0",
    ],
    python_requires=">=3.9",
)
