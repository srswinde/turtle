#!/usr/bin/env python

# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import distutils
import subprocess
from os.path import dirname, join

from setuptools import setup, find_packages


def read(*args):
    return open(join(dirname(__file__), *args)).read()


exec(open('turtle_db/version.py').read())

install_requires = [
]

tests_require = [
    'coverage',
    'flake8',
    'pydocstyle',
    'pylint',
    
]

exec(read('turtle_db', 'version.py'))


setup(name='turtle_db',
      version=__version__,  # noqa
      description='Webapp for cassini',
      long_description=read('README.rst'),
      author='Scott Swindell',
      author_email='srswinde@gmail.com',
      url='https://github.com/srswinde/turtle_db',
      classifiers=[
          'Development Status :: 2 - Alpha',
          'Intended Audience :: Developers',
          'Natural Language :: English',
          'Programming Language :: Python',
          'Programming Language :: Python :: 3.8',
          'Topic :: Internet'
      ],
      include_package_data=True,
      install_requires=install_requires,
      packages=find_packages(include=['turtle_db*']),
      test_suite='tests',
      setup_requires=['pytest-runner'],
      tests_require=tests_require,
      
)
