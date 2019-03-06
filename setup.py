import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="classifurlr",
    version="0.0.1",
    author="Justin Clark",
    author_email="jclark@cyber.harvard.edu",
    description="A tool to determine if a given web page or set of web pages is inaccessible",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/berkmancenter/py_classifurlr",
    packages=setuptools.find_packages(),
    classifiers=[ "Programming Language :: Python :: 3" ],
    install_requires=[
        'haralyzer',
        'beautifulsoup4',
        'numpy',
        'tldextract',
        'lxml',
        'python-dateutil',
        'cachetools',
    ]
)
