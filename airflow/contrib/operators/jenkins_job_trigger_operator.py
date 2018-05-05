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

import time
import socket
import json
from airflow.exceptions import AirflowException
from airflow.models import BaseOperator
from airflow.utils.decorators import apply_defaults
from airflow.contrib.hooks.jenkins_hook import JenkinsHook
import jenkins
from jenkins import JenkinsException
from six.moves.urllib.request import Request, urlopen
from six.moves.urllib.error import HTTPError, URLError

try:
    basestring
except NameError:
    basestring = str  # For python3 compatibility

class JenkinsJobTriggerOperator(BaseOperator):
    """
    Trigger a Jenkins Job and monitor it's execution.
    This operator depend on python-jenkins library,
    version >= 0.4.15 to communicate with jenkins server.
    You'll also need to configure a Jenkins connection in the connections screen.
    :param jenkins_connection_id: The jenkins connection to use for this job
    :type jenkins_connection_id: string
    :param job_name: The name of the job to trigger
    :type job_name: string
    :param parameters: The parameters block to provide to jenkins
    :type parameters: string
    :param sleep_time: How long will the operator sleep between each status
    request for the job (min 1, default 10)
    :type sleep_time: int
    :param max_try_before_job_appears: The maximum number of requests to make
        while waiting for the job to appears on jenkins server (default 10)
    :type max_try_before_job_appears: int
    """
    template_fields = ('parameters',)
    template_ext = ('.json',)
    ui_color = '#f9ec86'

    @apply_defaults
    def __init__(self,
                 jenkins_connection_id,
                 job_name,
                 parameters="",
                 sleep_time=10,
                 max_try_before_job_appears=10,
                 *args,
                 **kwargs):
        super(JenkinsJobTriggerOperator, self).__init__(*args, **kwargs)
        self.job_name = job_name
        self.parameters = parameters
        if sleep_time < 1:
            sleep_time = 1
        self.sleep_time = sleep_time
        self.jenkins_connection_id = jenkins_connection_id
        self.max_try_before_job_appears = max_try_before_job_appears

    def build_job(self, jenkins_server):
        """
        This function makes an API call to Jenkins to trigger a build for 'job_name'
        It returned a dict with 2 keys : body and headers.
        headers contains also a dict-like object which can be queried to get
        the location to poll in the queue.
        :param jenkins_server: The jenkins server where the job should be triggered
        :return: Dict containing the response body (key body)
        and the headers coming along (headers)
        """
        # Warning if the parameter is too long, the URL can be longer than
        # the maximum allowed size
        if self.parameters and isinstance(self.parameters, basestring):
            import ast
            self.parameters = ast.literal_eval(self.parameters)

        if not self.parameters:
            # We need a None to call the non parametrized jenkins api end point
            self.parameters = None
        _url = jenkins_server.build_job_url(self.job_name, self.parameters, None)
        self.log.info("_url : %s", _url)
        request = Request(_url, b'')
        return self.get_hook().jenkins_request_with_headers(jenkins_server, request)

    def poll_job_in_queue(self, location, jenkins_server):
        """
        This method poll the jenkins queue until the job is executed.
        When we trigger a job through an API call,
        the job is first put in the queue without having a build number assigned.
        Thus we have to wait the job exit the queue to know its build number.
        To do so, we have to add /api/json (or /api/xml) to the location
        returned by the build_job call and poll this file.
        When a 'executable' block appears in the json, it means the job execution started
        and the field 'number' then contains the build number.
        :param location: Location to poll, returned in the header of the build_job call
        :param jenkins_server: The jenkins server to poll
        :return: The build_number corresponding to the triggered job
        """
        try_count = 0
        # location = location + '/api/json'
        location = location + 'api/json'
        # TODO Use get_queue_info instead
        # once it will be available in python-jenkins (v > 0.4.15)
        self.log.info('Polling jenkins queue at the url %s', location)
        location = location.replace("http://", "https://")
        request = Request(location, b'')
        while try_count < self.max_try_before_job_appears:
            location_answer = self.get_hook().jenkins_request_with_headers(jenkins_server, request)
            if location_answer is not None:
                json_response = json.loads(location_answer['body'])
                if 'executable' in json_response:
                    build_number = json_response['executable']['number']
                    self.log.info('Job executed on Jenkins side with the build number %s',
                                  build_number)
                    return build_number
            try_count += 1
            time.sleep(self.sleep_time)
        raise AirflowException("The job hasn't been executed"
                               " after polling the queue %d times",
                               self.max_try_before_job_appears)

    def get_hook(self):
        return JenkinsHook(self.jenkins_connection_id)

    def execute(self, context):
        self.log.info("jenkins job execute")
        if not self.jenkins_connection_id:
            self.log.error(
                'Please specify the jenkins connection id to use.'
                'You must create a Jenkins connection before'
                ' being able to use this operator')
            raise AirflowException('The jenkins_connection_id parameter is missing,'
                                   'impossible to trigger the job')

        if not self.job_name:
            self.log.error("Please specify the job name to use in the job_name parameter")
            raise AirflowException('The job_name parameter is missing,'
                                   'impossible to trigger the job')

        self.log.info(
            'Triggering the job %s on the jenkins : %s with the parameters : %s',
            self.job_name, self.jenkins_connection_id, self.parameters)
        jenkins_server = self.get_hook().get_jenkins_server()
        self.log.info("whoami : %d", jenkins_server.jobs_count())
        self.log.info("jenkins_server: %s", jenkins_server)
        jenkins_response = self.build_job(jenkins_server)
        self.log.info("jenkins_response body: %s", jenkins_response["body"])
        self.log.info("jenkins_response headers: %s", jenkins_response["headers"])
        build_number = self.poll_job_in_queue(
            jenkins_response['headers']['Location'], jenkins_server)
        self.log.info("build_number: %s", build_number)

        time.sleep(self.sleep_time)
        keep_polling_job = True
        build_info = None
        while keep_polling_job:
            try:
                build_info = jenkins_server.get_build_info(name=self.job_name,
                                                           number=build_number)
                if build_info['result'] is not None:
                    keep_polling_job = False
                    # Check if job had errors.
                    if build_info['result'] != 'SUCCESS':
                        raise AirflowException(
                            'Jenkins job failed, final state : %s.'
                            'Find more information on job url : %s'
                            % (build_info['result'], build_info['url']))
                else:
                    self.log.info('Waiting for job to complete : %s , build %s',
                                  self.job_name, build_number)
                    time.sleep(self.sleep_time)
            except jenkins.NotFoundException as err:
                raise AirflowException(
                    'Jenkins job status check failed. Final error was: %s'
                    % err.resp.status)
            except jenkins.JenkinsException as err:
                raise AirflowException(
                    'Jenkins call failed with error : %s, if you have parameters '
                    'double check them, jenkins sends back '
                    'this exception for unknown parameters'
                    'You can also check logs for more details on this exception '
                    '(jenkins_url/log/rss)', str(err))
        if build_info:
            # If we can we return the url of the job
            # for later use (like retrieving an artifact)
            return build_info['url']
