#!/usr/bin/env python
import sys
import ConfigParser
import random
import fnmatch
import time
from argparse import ArgumentParser
from datetime import datetime
from contextlib import contextmanager
from os import makedirs, walk
from os.path import exists, expanduser
from shutil import copytree, rmtree

config_file = 'build.ini'
config = ConfigParser.ConfigParser()
ignore_patterns = []


def log(message):
    now = datetime.now()
    ts = (now.strftime('%H:%M:%S.%%(msec)03d') %
                       {'msec': int(now.strftime('%f')) / 1000})
    print('[%s] %s' % (ts, message))


@contextmanager
def step(name):
    log('=' * 75)
    log(name)
    yield
    log('')


def load_config(file):
    if not exists(file):
        sys.exit('Cannot find configuration file %s.' % file)
    
    config.read(file)


def config_val(section, option, default=None, method=config.get):
    if config.has_option(section, option):
        return method(section, option)

    return default


def config_bool(section, option, default=0):
    return config_val(section, option, default, config.getboolean)


def config_int(section, option, default=0):
    return config_val(section, option, default, config.getint)


def gen_git_ref(base_path=None, branch='master', prefix=None, max_length=64):
    log('checking ref on branch %s' % branch)
    ref = '' if not prefix else prefix
    ref_path = '.git/refs/heads/' + branch
    path = base_path + '/' + ref_path if base_path else ref_path
    if not exists(path):
        return None

    with(open(path)) as f:
        ref += f.readline().strip()[:max_length]

    return ref


def gen_random(prefix='', length=8):
    chars = 'BCDFGHJKLMNPQRSTVWXYZbcdfghjklmnpqrstvwxyz0123456789'
    return prefix + ''.join([random.choice(chars) for ii in xrange(length)])


def gen_datetime(prefix='', format='%Y%m%d%H%M%S'):
    return prefix + (str(int(time.time())) if format == 'ts'
                     else datetime.now().strftime(format))


def gen_version(type):
    log('generating %s version' % type)
    
    prefix = config_val('version', 'prefix', '')

    if type == 'git':
        path = expanduser(config_val('project', 'path', '.'))
        return gen_git_ref(base_path=path,
                           branch=config_val('project', 'branch', 'master'),
                           max_length=config_int('version', 'length', 64),
                           prefix=prefix)

    if type == 'random':
        return gen_random(prefix=prefix,
                          length=config_int('version', 'length', 8))

    if type == 'date':
        return gen_datetime(prefix, format=config_val('version', 'format',
                                                      '%Y%m%d%H%M%S'))


def pattern_list(pattern_str):
    if not pattern_str:
        return []
    return [p.strip() for p in pattern_str.split(',')]


def copy_check(dirname, contents):
    log(contents)
    match_sets = [fnmatch.filter(contents, ii) for ii in ignore_files]
    ignore = [item for sublist in match_sets for item in sublist]

    for ff in contents:
        if ff not in ignore:
            log('Copying %s' % ff)
    
    return ignore


def replace_text(target_dir, file_patterns, source_uri, target_uri):
    for root, folders, files in walk(target_dir):
        replace_sets = [fnmatch.filter(files, ii) for ii in file_patterns]
        replace_files = [ii for subset in replace_sets for ii in subset]

        for ff in files:
            if ff not in replace_files:
                continue

            path = '%s/%s' % (root, ff)
            log('Replacing in %s' % path)

            with(open(path)) as file:
                lines = file.readlines()

            lines = map(lambda s: s.replace(source_uri, target_uri), lines)
            with(open(path, 'w')) as file:
                file.writelines(lines)


def set_memcache_key(version, target_uri):
    try:
        import pylibmc
    except Exception as e:
        sys.exit('Cannot import pylibmc. Please ensure it is installed.')
    
    uri = '%s:%d' % (config_val('output', 'memcache_host'),
                     config_int('output', 'memcache_port'))
    cache = pylibmc.Client([uri], binary=False)
    
    for (config_key, value) in (('memcache_version_key', version),
                                ('memcache_uri_key', target_uri)):
        key_name = config_val('output', config_key)
        if key_name:
            log('Setting memcache key %s to %s' % (key_name, value))
            cache.set(key_name, value)


def set_redis_key(version, target_uri):
    try:
        import redis
    except Exception as e:
        sys.exit('Cannot import redis (redis-py). '
                 'Please ensure it is installed.')
    
    cache = redis.StrictRedis(host=config_val('output', 'redis_host'),
                              port=config_int('output', 'redis_port'))

    for (config_key, value) in (('redis_version_key', version),
                                ('redis_uri_key', target_uri)):
        key_name = config_val('output', config_key)
        if key_name:
            log('Setting redis key %s to %s' % (key_name, value))
            cache.set(key_name, value)


def write_files(version, target_uri):
    for (config_key, contents) in (('version_file', version),
                                   ('uri_file', target_uri)):
        file_name = expanduser(config_val('output', config_key, ''))
        if not file_name:
            continue
        with(open(file_name, 'w')) as file:
            log('Writing file %s with %s' % (file_name, contents))
            file.write(contents)


if __name__ == '__main__':
    parser = ArgumentParser('Builds static assets.')
    parser.add_argument('-c', '--config-file', metavar='FILE',
                        default=config_file)
    args = parser.parse_args()
    load_config(args.config_file)

    overwrite = config_bool('project', 'overwrite', False)
    source_dir = '%s/%s' % (
            expanduser(config_val('project', 'path')).rstrip('/'),
            config_val('project', 'source_dir').lstrip('/'))

    if not exists(source_dir):
        sys.exit('Source path does not exist: %s' % source_dir)

    with step('Generating version...'):
        version_type = config_val('version', 'type')
        version = gen_version(version_type)

        target_dir = expanduser(config_val('project', 'target_dir', ''))
        target_dir = target_dir.replace('{version}', version)

        log('version = %s' % version)
        log('source = %s' % source_dir)
        log('target = %s' % target_dir)

    with step('Performing file copy...'):
        ignore_files = pattern_list(config_val('project', 'ignore_files'))

        if exists(target_dir):
            if not overwrite:
                sys.exit('Target already exists: %s' % target_dir)

            log('Removing existing target: %s' % target_dir)
            rmtree(target_dir)

        if not exists(target_dir):
            log('Creating target: %s' % target_dir)
            copytree(source_dir, target_dir, symlinks=False,
                     ignore=copy_check)

    with step('Substituting URIs in files...'):
        source_uri = config_val('project', 'source_uri')
        target_uri = config_val('project', 'target_uri').format(
            version=version)
        replace_files = pattern_list(config_val('project', 'replace_files'))

        if source_uri != target_uri:
            log('Replacing instances of %s with %s...' % (source_uri,
                                                          target_uri))
            replace_text(target_dir, replace_files, source_uri, target_uri)

    with step('Writing version and path to output targets...'):
        set_memcache_key(version, target_uri)
        set_redis_key(version, target_uri)
        write_files(version, target_uri)
