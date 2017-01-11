Classifurlr
===========

Description
-----------

Classifurlr is a tool to automatically determine if a given web page or set of
web pages is likely inaccessible.

This tool does not actually fetch any content from the Internet - previously
fetched content is fed into it. The given content is moved through a pipeline
of classifiers, each of which looks for different signatures of
inaccessibility.  Each classifier returns how confident it is that the given
content is inaccessible. These are then pooled to create a final accessibility
verdict.

Right now, the following classifiers are implemented:

* _ClassifyPipeline_ - a classifier that pools the results of other classifiers
* _StatusCodeClassifier_ - a simple classifier that says all non-2xx status codes are down
* _ErrorClassifier_ - classifies all sessions that contain errors as down
* _ThrottleClassifier_ - detects excessively long load times that might
 indicate throttling
* _CosineSimilarityClassifier_ - uses cosine similarity between a page and
 a baseline to determine whether a page is unexpected content (like a block
 page). Relevant paper [here](http://conferences.sigcomm.org/imc/2014/papers/p299.pdf)
* _PageLengthClassifier_ - detects whether a page contains unexpected content
 (like a block page) by looking at the different lengths of a given page and
 a baseline. Relevant paper
 [here](http://conferences.sigcomm.org/imc/2014/papers/p299.pdf)
* _DifferingDomainClassifier_ - detects whether the requested domain and the
 final domain are significantly different which could indicate DNS tampering
 or injected redirects.

Requirements
------------

* Python 3.x
* pip

Getting Started
---------------

After making sure you have the requirements, install the rest of the
dependencies with:

```
pip install -r requirements.txt
```

The tool can be used in three ways: as a Python module, as a command line
program, and as a web service.

To run Classifurlr as a command line tool, simply run:

```
python classifurlr.py <name of data file>
```
You can see more options by adding the `-h` flag to the above command.

The data file should be a JSON file with the following structure:
```
{
  url: 'http://example.com',
  baseline: false, // 'page_1',
  pageDetail: {
      'page_0': {
          asn: 0,
          screenshot: 'data:image/png;base64,',
          errors: [''],
      },
      ...
  },
  har: {...}
}
```
More details and field definitions for this structure are [in the
wiki](todo).

The tool will return a JSON document that looks like this:
```
{
  "status": "down",
  "statusConfidence": 0.52,
  "classifier": "classification_pipeline",
  "constituents": [
    {
      "status": "down",
      "statusConfidence": 0.4,
      "classifier": "page_length"
    },
    ...
```
Here are the field definitions:
* _status_ - the determined status of the page, `up` or `down`. Right now,
 **this will always return `down`**.
* _statusConfidence_ - how confident the tool is that the given set of pages
 are inaccessible on a scale from 0.0 to 1.0.
* _classifier_ - the name of the classifier that returned this result
* _constituents_ - if the classifier used other classifiers to determine its
 result, this will contain an array of documents that have the same form as
 this document.

Classifurlr also minimally complies to the WSGI spec with the provided `app`
function. To run the tool as a web service, run something like the following:
```
gunicorn classifurlr:app
```

Code Repository
---------------

Code is hosted on GitHub at
[https://github.com/berkmancenter/classifurlr](https://github.com/berkmancenter/classifurlr)


Tested Configurations
---------------------

This has been tested with Python 3.6.0 running on Ubuntu 16.04.

Running Tests
-------------

Classifurlr comes with a really minimal test suite. To run it, just run:
```
python classifurlr_test.py
```

Issue Tracker
-------------

TODO

Contributors
------------

jdcc

License
-------

Copyright © 2017 President and Fellows of Harvard College

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
