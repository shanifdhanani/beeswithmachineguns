#!/bin/env python

"""
The MIT License

Copyright (c) 2010 The Chicago Tribune & Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from multiprocessing import Pool
import os
import re
import socket
import sys
import time
import urllib2

import boto.ec2

from Crypto.Util.randpool import RandomPool_DeprecationWarning
import warnings
warnings.filters.append(('ignore', None, RandomPool_DeprecationWarning, None, 0))
import paramiko

from Crypto import Random

EC2_INSTANCE_TYPE = 't1.micro'
EC2_REGIONAL_AMIS = {'us-east-1': 'ami-3202f25b', 'us-west-1': 'ami-f5bfefb0', 'eu-west-1': 'ami-3d1f2b49', 'ap-southeast-1': 'ami-f092eca2'}
PACKAGES_TO_INSTALL = 'apache2-utils'
STATE_FILENAME = os.path.expanduser('~/.bees')

# Utilities

def _read_server_list():
    if not os.path.isfile(STATE_FILENAME):
        return (None, None, None, None)

    with open(STATE_FILENAME, 'r') as f:
        username = f.readline().strip()
        key_name = f.readline().strip()
        region = f.readline().strip()
        text = f.read()
        instance_ids = [item for item in text.split('\n') if item != '']

        print 'Read %i bees from the roster.' % len(instance_ids)

    return (username, key_name, region, instance_ids)

def _write_credentials(username, key_name, region):
    with open(STATE_FILENAME, 'w') as f:
        f.write('%s\n' % username)
        f.write('%s\n' % key_name)
        f.write('%s\n' % region)

def _append_server_list(instance_id):
    with open(STATE_FILENAME, 'a') as f:
        f.write('%s\n' % instance_id)

def _delete_server_list():
    os.remove(STATE_FILENAME)

def _get_pem_path(key):
    return os.path.expanduser('~/.ssh/%s.pem' % key)

def _prepare_instances(params):
    print "Seting up bee %s @ %s." % (params['instance_id'],  params['instance_ip'])

    time.sleep(20)
    Random.atfork()

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            params['instance_ip'],
            username=params['username'],
            key_filename=_get_pem_path(params['key_name']))

        install_ab_stdin, install_ab_stdout, install_ab_stderr = client.exec_command('sudo apt-get install -y  %s' % PACKAGES_TO_INSTALL)
        ab_installation = install_ab_stdout.read()
        time.sleep(5)
        verify_ab_stdin, verify_ab_stdout, verify_ab_stderr = client.exec_command('sudo ab')
        verify_ab_stderr = verify_ab_stderr.read()
        client.close()

        if re.search("Usage: ab \[options\] \[http\[s\]\:\/\/\]hostname\[\:port\]\/path", verify_ab_stderr):
            _append_server_list(params['instance_id'])
            print 'Bee %s is ready for the attack.' % params['instance_id']
            return True
    except Exception, e:
        print "Exception: %s" % e

    return False

# Methods

def up(count, group, zone, image_id, username, key_name):
    """
    Startup the load testing server.
    """
    existing_username, existing_key_name, existing_region, instance_ids = _read_server_list()

    if instance_ids:
        print 'Bees are already assembled and awaiting orders.'
        return

    count = int(count)

    pem_path = _get_pem_path(key_name)

    if not os.path.isfile(pem_path):
        print 'No key file found at %s' % pem_path
        return

    print 'Connecting to the hive.'

    region = re.match('([a-z]{2}-[a-z]*-\d)', zone).group(0)
    ec2_connection = boto.ec2.connect_to_region(region)

    print 'Attempting to call up %i bees.' % count

    if not image_id:
        image_id = EC2_REGIONAL_AMIS[region]

    reservation = ec2_connection.run_instances(
        image_id=image_id,
        min_count=count,
        max_count=count,
        key_name=key_name,
        security_groups=[group],
        instance_type=EC2_INSTANCE_TYPE,
        placement=zone)

    print 'Waiting for bees to load their machine guns...'

    for instance in reservation.instances:
        while instance.state != 'running':
            print '.'
            time.sleep(5)
            instance.update()

    _write_credentials(username, key_name, region)

    params = []
    for instance in reservation.instances:
        params.append({
            'instance_ip': instance.ip_address,
            'instance_id': instance.id,
            'username': username,
            'key_name': key_name,
        })

    pool = Pool(len(params))
    result = pool.map(_prepare_instances, params)

    if False not in result:
        print 'The swarm has assembled %i bees.' % len(reservation.instances)
    else:
        terminated_instance_ids = ec2_connection.terminate_instances(
            instance_ids=[instance['instance_id'] for instance in params])
        _delete_server_list()
        print 'Assembly of the swarm has failed.'

def report():
    """
    Report the status of the load testing servers.
    """
    username, key_name, region, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    ec2_connection = boto.ec2.connect_to_region(region)

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    for instance in instances:
        print 'Bee %s: %s @ %s' % (instance.id, instance.state, instance.ip_address)

def down():
    """
    Shutdown the load testing server.
    """
    username, key_name, region, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees have been mobilized.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.ec2.connect_to_region(region)

    print 'Calling off the swarm.'

    terminated_instance_ids = ec2_connection.terminate_instances(
        instance_ids=instance_ids)

    print 'Stood down %i bees.' % len(terminated_instance_ids)

    _delete_server_list()

def _attack(params):
    """
    Test the target URL with requests.

    Intended for use with multiprocessing.
    """
    print 'Bee %i is joining the swarm.' % params['i']

    Random.atfork()

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            params['instance_name'],
            username=params['username'],
            key_filename=_get_pem_path(params['key_name']))

        print 'Bee %i is firing his machine gun. Bang bang! (%s)' % (params['i'], params['url'])

        stdin, stdout, stderr = client.exec_command('ab -r -n %(num_requests)s -c %(concurrent_requests)s -C "sessionid=NotARealSessionID" %(url)s' % params)

        response = {}

        ab_results = stdout.read()
        ms_per_request_search = re.search('Time\ per\ request:\s+([0-9.]+)\ \[ms\]\ \(mean\)', ab_results)

        if not ms_per_request_search:
            print 'Bee %i lost sight of the target (connection timed out).' % params['i']
            return None

        requests_per_second_search = re.search('Requests\ per\ second:\s+([0-9.]+)\ \[#\/sec\]\ \(mean\)', ab_results)
        fifty_percent_search = re.search('\s+50\%\s+([0-9]+)', ab_results)
        ninety_percent_search = re.search('\s+90\%\s+([0-9]+)', ab_results)
        complete_requests_search = re.search('Complete\ requests:\s+([0-9]+)', ab_results)

        response['ms_per_request'] = float(ms_per_request_search.group(1))
        response['requests_per_second'] = float(requests_per_second_search.group(1))
        response['fifty_percent'] = float(fifty_percent_search.group(1))
        response['ninety_percent'] = float(ninety_percent_search.group(1))
        response['complete_requests'] = float(complete_requests_search.group(1))

        print 'Bee %i is out of ammo.' % params['i']

        client.close()

        return response
    except socket.error, e:
        return e

def _print_results(results):
    """
    Print summarized load-testing results.
    """
    timeout_bees = [r for r in results if r is None]
    exception_bees = [r for r in results if type(r) == socket.error]
    complete_bees = [r for r in results if r is not None and type(r) != socket.error]

    num_timeout_bees = len(timeout_bees)
    num_exception_bees = len(exception_bees)
    num_complete_bees = len(complete_bees)

    if exception_bees:
        print '     %i of your bees didn\'t make it to the action. They might be taking a little longer than normal to find their machine guns, or may have been terminated without using "bees down".' % num_exception_bees

    if timeout_bees:
        print '     Target timed out without fully responding to %i bees.' % num_timeout_bees

    if num_complete_bees == 0:
        print '     No bees completed the mission. Apparently your bees are peace-loving hippies.'
        return

    complete_results = [r['complete_requests'] for r in complete_bees]
    total_complete_requests = sum(complete_results)
    print '     Complete requests:\t\t%i' % total_complete_requests

    complete_results = [r['requests_per_second'] for r in complete_bees]
    mean_requests = sum(complete_results)
    print '     Requests per second:\t%f [#/sec] (mean)' % mean_requests

    complete_results = [r['ms_per_request'] for r in complete_bees]
    mean_response = sum(complete_results) / num_complete_bees
    print '     Time per request:\t\t%f [ms] (mean)' % mean_response

    complete_results = [r['fifty_percent'] for r in complete_bees]
    mean_fifty = sum(complete_results) / num_complete_bees
    print '     50%% response time:\t\t%f [ms] (mean)' % mean_fifty

    complete_results = [r['ninety_percent'] for r in complete_bees]
    mean_ninety = sum(complete_results) / num_complete_bees
    print '     90%% response time:\t\t%f [ms] (mean)' % mean_ninety

    if mean_response < 500:
        print 'Mission Assessment: Target crushed bee offensive.'
    elif mean_response < 1000:
        print 'Mission Assessment: Target successfully fended off the swarm.'
    elif mean_response < 1500:
        print 'Mission Assessment: Target wounded, but operational.'
    elif mean_response < 2000:
        print 'Mission Assessment: Target severely compromised.'
    else:
        print 'Mission Assessment: Swarm annihilated target.'

def attack(url, n, c):
    """
    Test the root url of this site.
    """
    username, key_name, region, instance_ids = _read_server_list()

    if not instance_ids:
        print 'No bees are ready to attack.'
        return

    print 'Connecting to the hive.'

    ec2_connection = boto.ec2.connect_to_region(region)

    print 'Assembling bees.'

    reservations = ec2_connection.get_all_instances(instance_ids=instance_ids)

    instances = []

    for reservation in reservations:
        instances.extend(reservation.instances)

    instance_count = len(instances)
    requests_per_instance = int(float(n) / instance_count)
    connections_per_instance = int(float(c) / instance_count)

    print 'Each of %i bees will fire %s rounds, %s at a time.' % (instance_count, requests_per_instance, connections_per_instance)

    params = []

    urls = url.split(",")
    last_url = ""
    for i, instance in enumerate(instances):
        last_url = urls[i % len(urls)] or last_url
        params.append({
            'i': i,
            'instance_id': instance.id,
            'instance_name': instance.public_dns_name,
            'url': last_url,
            'concurrent_requests': connections_per_instance,
            'num_requests': requests_per_instance,
            'username': username,
            'key_name': key_name,
        })

    print 'Stinging URL so it will be cached for the attack.'

    # Ping url so it will be cached for testing
    urllib2.urlopen(url)

    print 'Organizing the swarm.'

    # Spin up processes for connecting to EC2 instances
    pool = Pool(len(params))
    results = pool.map(_attack, params)

    print 'Offensive complete.'

    _print_results(results)

    print 'The swarm is awaiting new orders.'