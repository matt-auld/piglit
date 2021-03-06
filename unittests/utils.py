# Copyright (c) 2014 Intel Coporation

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

""" Common helpers for framework tests

This module collects common tools that are needed in more than one test module
in a single place.

"""

from __future__ import (
    absolute_import, division, print_function, unicode_literals
)
import os
import sys
import copy
import shutil
import tempfile as tempfile_
import functools
import subprocess
import errno
import importlib
from contextlib import contextmanager

try:
    import simplejson as json
except ImportError:
    import json
from nose.plugins.skip import SkipTest
import six
try:
    from six.moves import getcwd
except ImportError:
    # pylint: disable=no-member
    if six.PY2:
        getcwd = os.getcwdu
    elif six.PY3:
        getcwd = os.getcwd
    # pylint: enable=no-member

from framework import test, backends, core, results, compat

__all__ = [
    'tempfile',
    'resultfile',
    'tempdir',
    'JSON_DATA'
]

core.get_config()

_WRITE_MODE = 'w'
_READ_MODE = 'r'


class _Tree(dict):
    """Private helper to make JSON_DATA easier to work with."""
    def __getitem__(self, key):
        try:
            return super(_Tree, self).__getitem__(key)
        except KeyError:
            ret = self[key] = _Tree()
            return ret


JSON_DATA = {
    "options": {
        "profile": "tests/fake.py",
        "filter": [],
        "exclude_filter": []
    },
    "results_version": backends.json.CURRENT_JSON_VERSION,
    "name": "fake-tests",
    "lspci": "fake",
    "glxinfo": "fake",
    "tests": _Tree({
        "sometest": {
            'result': 'pass',
            'time': 1.2,
        }
    })
}

_SAVED_COMPRESSION = os.environ.get('PIGLIT_COMPRESSION')


@compat.python_2_unicode_compatible
class TestFailure(AssertionError):
    """An exception to be raised when a test fails.

    Nose expects an AssertionError for test failures, so this is a sublcass of
    AssertionError.

    It provides the benefit of being able to take either a text message to
    print, or an exception instance. When passed text it will print the message
    exactly, when passed an exception it will print the exception type and the
    str() value of that exception.

    """
    def __init__(self, arg):
        super(TestFailure, self).__init__(self)
        self.__arg = arg

    def __str__(self):
        if isinstance(self.__arg, Exception):
            return u'exception type "{}" with message "{}" raised.'.format(
                type(self.__arg), str(self.__arg))
        else:
            return self.__arg


class UtilsError(Exception):
    """ An exception to be raised by utils """
    pass


class SentinalException(Exception):
    """An exception to be used as a sentinal."""
    pass


class StaticDirectory(object):
    """ Helper class providing shared files creation and cleanup

    One should override the setup_class method in a child class, call super(),
    and then add files to cls.dir.

    Tests in this class should NOT modify the contents of tidr, if you want
    that functionality you want a different class

    """
    @classmethod
    def setup_class(cls):
        """ Create a temperary directory that will be removed in teardown_class
        """
        cls.tdir = tempfile_.mkdtemp()

    @classmethod
    def teardown_class(cls):
        """ Remove the temporary directory """
        shutil.rmtree(cls.tdir)


class DocFormatter(object):
    """Decorator for formatting object docstrings.

    This class is designed to be initialized once per test module, and then one
    instance used as a decorator for all functions.

    Works as follows:
    >>> doc_formatter = DocFormatter({'format': 'foo', 'name': 'bar'})
    >>>
    >>> @doc_formatter
    ... def foo():
    ...     '''a docstring for {format} and {name}'''
    ...     pass
    ...
    >>> foo.__doc__
    'a docstring for foo and bar'

    This allows tests that can be dynamically updated by changing a single
    constant to have the test descriptions alos updated by the same constant.

    Arguments:
    table -- a dictionary of key value pairs to be converted

    """
    def __init__(self, table):
        self.__table = table

    def __call__(self, func):
        try:
            func.__doc__ = func.__doc__.format(**self.__table)
        except KeyError as e:
            # We want to catch this to ensure that a test that is looking for a
            # KeyError doesn't pass when it shouldn't
            raise UtilsError(e)

        return func


class Test(test.Test):
    """A basic dmmmy Test class that can be used in places a test is required.

    This provides dummy version of abstract methods in
    framework.test.base.Test, which allows it to be initialized and run, but is
    unlikely to be useful for running.

    This is mainly intended for testing where a Test class is required, but
    doesn't need to be run.

    """
    def interpret_result(self):
        pass


class GeneratedTestWrapper(object):
    """ An object proxy for nose test instances

    Nose uses python generators to create test generators, the drawback of this
    is that unless the generator is very specifically engineered it yeilds the
    same instance multiple times. Since nose uses an instance attribute to
    display the name of the test on a failure, and it prints the failure
    dialogue after the run is complete all failing tests from a single
    generator will end up with the name of the last test generated. This
    generator is used in conjunction with the nose_generator() decorator to
    create multiple objects each with a unique description attribute, working
    around the bug.
    Upstream bug: https://code.google.com/p/python-nose/issues/detail?id=244

    This uses functoos.update_wrapper to proxy the underlying object, and
    provides a __call__ method (which allows it to be called like a function)
    that calls the underling function.

    This class can also be used to wrap a class, but that class needs to
    provide a __call__ method, and use that to return results.

    Arguments:
    wrapped -- A function or function-like-class

    """
    def __init__(self, wrapped):
        self._wrapped = wrapped
        functools.update_wrapper(self, self._wrapped)

    def __call__(self, *args, **kwargs):
        """ calls the wrapped function

        Arguments:
        *args -- arguments to be passed to the wrapped function
        **kwargs -- keyword arguments to be passed to the wrapped function
        """
        return self._wrapped(*args, **kwargs)


@contextmanager
def resultfile():
    """ Create a stringio with some json in it and pass that as results """
    data = copy.deepcopy(JSON_DATA)
    data['tests']['sometest'] = results.TestResult('pass')
    data['tests']['sometest'].time = 1.2
    data = results.TestrunResult.from_dict(data)
    with tempfile_.NamedTemporaryFile(mode=_WRITE_MODE, delete=False) as f:
        json.dump(data, f, default=backends.json.piglit_encoder)

    try:
        yield f
    finally:
        os.remove(f.name)


@contextmanager
def tempfile(contents):
    """ Provides a context manager for a named tempfile

    This contextmanager creates a named tempfile, writes data into that
    tempfile, then closes it and yields the filepath. After the context is
    returned it closes and removes the tempfile.

    Arguments:
    contests -- This should be a string (unicode or str), in which case it is
                written directly into the file.

    """
    # Do not delete the tempfile as soon as it is closed
    temp = tempfile_.NamedTemporaryFile(mode=_WRITE_MODE, delete=False)
    temp.write(contents)
    temp.close()

    try:
        yield temp.name
    finally:
        os.remove(temp.name)


@contextmanager
def tempdir():
    """ Creates a temporary directory, returns it, and then deletes it """
    tdir = tempfile_.mkdtemp()
    try:
        yield tdir
    finally:
        shutil.rmtree(tdir)


def nose_generator(func):
    """ Decorator for nose test generators to us GeneratedTestWrapper

    This decorator replaces each function yeilded by a test generator with a
    GeneratedTestWrapper reverse-proxy object

    """

    @functools.wraps(func)
    def test_wrapper(*args, **kwargs):
        for x in func(*args, **kwargs):
            x = list(x)
            x[0] = GeneratedTestWrapper(x[0])
            yield tuple(x)  # This must be a tuple for some reason

    return test_wrapper


def binary_check(bin_, errno_=None):
    """Check for the existence of a binary or raise SkipTest.

    If an errno_ is provided then a skip test will be raised unless the error
    number provided is raised, or no error is raised.

    """
    with open(os.devnull, 'w') as null:
        try:
            subprocess.check_call([bin_], stdout=null, stderr=null)
        except OSError as e:
            if e.errno == errno.ENOENT:
                raise SkipTest('Binary {} not available'.format(bin_))
        except subprocess.CalledProcessError as e:
            if errno_ is not None and e.returncode == errno_:
                pass
            else:
                raise SkipTest(
                    'Binary provided bad returncode of {} (wanted {})'.format(
                        e.returncode, errno_))


def module_check(module):
    """Check that an external module is available or skip."""
    try:
        importlib.import_module(module)
    except ImportError:
        raise SkipTest('Required module {} not available.'.format(module))


def platform_check(plat):
    """If the platform is not in the list specified skip the test."""
    if not sys.platform.startswith(plat):
        raise SkipTest('Platform {} is not supported'.format(sys.platform))


def test_in_tempdir(func):
    """Decorator that moves to a new directory to run a test.

    This decorator ensures that the test moves to a new directory, and then
    returns to the original directory after the test completes.

    """
    original_dir = getcwd()

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with tempdir() as tdir:
            try:
                os.chdir(tdir)
                return func(*args, **kwargs)
            finally:
                os.chdir(original_dir)

    return wrapper


def not_raises(exceptions):
    """Decorator that causes a test to fail if it raises an exception.

    Without arguments this will raise a TestFailure if Exception is raised.
    arguments are passed those will be handed directly to the except keyword,
    and a TestFailure will be raised only for those exceptions.

    Uncaught exceptions will still be handled by nose and return an error.

    """

    def _wrapper(func):
        """wrapper that wraps the actual functiion definition."""

        @functools.wraps(func)
        def _inner(*args, **kwargs):
            """Wrapper for the function call."""
            try:
                func(*args, **kwargs)
            except exceptions as e:
                raise TestFailure(e)

        return _inner

    return _wrapper


def no_error(func):
    """Convenience wrapper around not_raises when any error is an exception

    """
    return not_raises(Exception)(func)


def capture_stderr(func):
    """Redirect stderr to stdout for nose capture.

    It would probably be better to implement a full stderr handler as a
    plugin...

    """
    @functools.wraps(func)
    def _inner(*args, **kwargs):
        restore = sys.stderr
        sys.stderr = sys.stdout
        try:
            func(*args, **kwargs)
        finally:
            sys.stderr = restore

    return _inner


def set_env(**envargs):
    """Decorator that sets environment variables and then unsets them.

    If an value is set to None that key will be deleted from os.environ

    """

    def _decorator(func):
        """The actual decorator."""

        @functools.wraps(func)
        def _inner(*args, **kwargs):
            """The returned function."""
            backup = {}
            for key, value in six.iteritems(envargs):
                backup[key] = os.environ.get(key, "__DONOTRESTORE__")
                if value is not None:
                    os.environ[key] = value
                elif key in os.environ:
                    del os.environ[key]

            try:
                func(*args, **kwargs)
            finally:
                for key, value in six.iteritems(backup):
                    if value == "__DONOTRESTORE__" and key in os.environ:
                        del os.environ[key]
                    elif value != '__DONOTRESTORE__':
                        os.environ[key] = value

        return _inner

    return _decorator


def set_piglit_conf(*values):
    """Decorator that sets and then usets values from core.PIGLIT_CONF.

    This decorator takes arguments for sections and options to overwrite in
    piglit.conf. It will first backup any options to be overwritten, and store
    any options that don't exist. Then it will set those options, run the test,
    and finally restore any options overwritten, and delete any new options
    added. If value is set to NoneType the option will be removed.

    Arguments:
    Values -- tuples containing a section, option, and value in the form:
    (<section>, <option>, <value>)

    """
    def _decorator(func):
        """The actual decorator."""

        @functools.wraps(func)
        def _inner(*args, **kwargs):
            """The function returned by the decorator."""
            backup = set()
            remove = set()

            for section, key, value in values:
                get = core.PIGLIT_CONFIG.safe_get(section, key)
                # If there is a value, save that value to restore it, if there
                # is not a value AND if there is a value to set (IE: we're not
                # clearing a value if it exsists), the add it to remove
                if get is not None:
                    backup.add((section, key, get))
                elif value is not None:
                    remove.add((section, key))

                # set any new values, and remove any values that are set to
                # None
                if value is not None:
                    if not core.PIGLIT_CONFIG.has_section(section):
                        core.PIGLIT_CONFIG.add_section(section)
                    core.PIGLIT_CONFIG.set(section, key, value)
                elif (core.PIGLIT_CONFIG.has_section(section) and
                      core.PIGLIT_CONFIG.has_option(section, key)):
                    core.PIGLIT_CONFIG.remove_option(section, key)

            try:
                func(*args, **kwargs)
            finally:
                # Restore all values
                for section, key, value in backup:
                    core.PIGLIT_CONFIG.set(section, key, value)
                for section, key in remove:
                    core.PIGLIT_CONFIG.remove_option(section, key)

        return _inner
    return _decorator


def set_compression(mode):
    """lock piglit compression mode to specifed value.

    Meant to be called from setup_module function.

    """
    # The implimentation details of this is that the environment value is the
    # first value looked at for setting compression, so if it's set all other
    # values will be ignored.
    os.environ['PIGLIT_COMPRESSION'] = mode


def unset_compression():
    """Restore compression to the origonal value.

    Counterpart to set_compression. Should be called from teardown_module.

    """
    if _SAVED_COMPRESSION is not None:
        os.environ['PIGLIT_COMPRESSION'] = _SAVED_COMPRESSION
    else:
        del os.environ['PIGLIT_COMPRESSION']
