# Copyright 2019 Jake Magers
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import setuptools

setuptools.setup(
    name='chanpy',
    version='0.0.2',
    author='Jake Magers',
    author_email='jmagers12@gmail.com',
    description='A CSP library based on Clojure core.async',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/JMagers/chanpy',
    packages=setuptools.find_packages(),
    license='Apache License 2.0',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: Apache Software License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.7',
)
