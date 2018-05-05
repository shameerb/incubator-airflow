# -*- coding: utf-8 -*-
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
# 
#   http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

from airflow.hooks.base_hook import BaseHook
from six.moves.urllib.request import Request, urlopen
from six.moves.urllib.error import HTTPError, URLError

import jenkins
import distutils
import base64
import socket


class JenkinsHook(BaseHook):
    """
    Hook to manage connection to jenkins server
    """

    def __init__(self, conn_id='jenkins_default'):
        connection = self.get_connection(conn_id)
        self.connection = connection
        connectionPrefix = 'https'
        # connection.extra contains info about using https (true) or http (false)
        if connection.extra is None or connection.extra == '':
            connection.extra = 'false'
            # set a default value to connection.extra
            # to avoid rising ValueError in strtobool
        # if distutils.util.strtobool(connection.extra):
            # connectionPrefix = 'https'
        # url = '%s://%s:%d' % (connectionPrefix, connection.host, connection.port)
        url = '%s://%s/jenkins' % (connectionPrefix, connection.host)
        self.log.info('Trying to connect to %s with %s:%s', url, connection.login, connection.password)
        self.jenkins_server = jenkins.Jenkins(url, connection.login, connection.password)

    def get_jenkins_server(self):
        return self.jenkins_server


    # TODO Use jenkins_urlopen instead when it will be available
    # in the stable python-jenkins version (> 0.4.15)
    def jenkins_request_with_headers(self, jenkins_server, req, add_crumb=True):
        """
        We need to get the headers in addition to the body answer
        to get the location from them
        This function is just a copy of the one present in python-jenkins library
        with just the return call changed
        :param jenkins_server: The server to query
        :param req: The request to execute
        :param add_crumb: Boolean to indicate if it should add crumb to the request
        :return:
        """
        try:
            # if jenkins_server.auth:
            #     req.add_header('Authorization', jenkins_server.auth)
            # if add_crumb:
            #     jenkins_server.maybe_add_crumb(req)
            base64string = base64.b64encode('%s:%s' % (self.connection.login, self.connection.password))
            self.log.info("base64string -> %s", base64string)
            req.add_header("Authorization", "Basic %s" % base64string)  
            response = urlopen(req, timeout=jenkins_server.timeout)
            response_body = response.read()
            response_headers = response.info()
            if response_body is None:
                raise jenkins.EmptyResponseException(
                    "Error communicating with server[%s]: "
                    "empty response" % jenkins_server.server)
            return {'body': response_body.decode('utf-8'), 'headers': response_headers}
        except HTTPError as e:
            # Jenkins's funky authentication means its nigh impossible to
            # distinguish errors.
            if e.code in [401, 403, 500]:
                # six.moves.urllib.error.HTTPError provides a 'reason'
                # attribute for all python version except for ver 2.6
                # Falling back to HTTPError.msg since it contains the
                # same info as reason
                raise JenkinsException(
                    'Error in request. ' +
                    'Possibly authentication failed [%s]: %s' % (
                        e.code, e.msg)
                )
            elif e.code == 404:
                raise jenkins.NotFoundException('Requested item could not be found')
            else:
                raise
        except socket.timeout as e:
            raise jenkins.TimeoutException('Error in request: %s' % e)
        except URLError as e:
            # python 2.6 compatibility to ensure same exception raised
            # since URLError wraps a socket timeout on python 2.6.
            if str(e.reason) == "timed out":
                raise jenkins.TimeoutException('Error in request: %s' % e.reason)
            raise JenkinsException('Error in request: %s' % e.reason)
