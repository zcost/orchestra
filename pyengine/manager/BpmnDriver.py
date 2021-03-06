import json
import requests
import uuid
import os

from django.contrib.auth.hashers import make_password
from pyengine.lib import utils
from pyengine.lib import config
from pyengine.lib.error import *
from pyengine.manager import Manager 

from io import BytesIO

from SpiffWorkflow import Task
from SpiffWorkflow.bpmn.BpmnWorkflow import BpmnWorkflow
from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.parser.TaskParser import TaskParser
from SpiffWorkflow.bpmn.parser.util import *
from SpiffWorkflow.bpmn.specs.BpmnSpecMixin import BpmnSpecMixin
from SpiffWorkflow.bpmn.specs.UserTask import UserTask
from SpiffWorkflow.bpmn.storage.BpmnSerializer import BpmnSerializer
from SpiffWorkflow.bpmn.storage.CompactWorkflowSerializer import CompactWorkflowSerializer
from SpiffWorkflow.bpmn.storage.Packager import Packager

from SpiffWorkflow.specs import WorkflowSpec
from SpiffWorkflow.specs.Simple import Simple

import logging
LOG = logging.getLogger(__name__)

class PyengineBpmnWorkflow(BpmnWorkflow):
    def __init__(self, workflow_spec, workflow_id, stack_id, ctx, **kwargs):
        """
        constructor
        """
        super(PyengineBpmnWorkflow, self).__init__(workflow_spec, name=workflow_id, script_engine=None, read_only=False, **kwargs)
        self.stack_id = stack_id
        self.workflow_id = workflow_id
        self.ctx = ctx

class ServiceTask(Simple, BpmnSpecMixin, Manager):
    """
    Task Spec for a bpmn:serviceTask node.
    """
    def is_engine_task(self):
        return False

    def entering_ready_state(self, task):
        self.logger.debug("###### enter ready : %s" % task.get_description())
        stack_id = task.workflow.stack_id
        p_mgr = self.locator.getManager('PackageManager')
        state = {'workflow_state':{task.get_description():'ready'}}
        p_mgr.addEnv2(stack_id, state)

    def entering_complete_state(self, task):
        self.logger.debug("###### enter complete : %s" % task.get_description())

        # TODO: find exact url
        url = 'http://127.0.0.1/api/v1/catalog/workflows/%s/tasks' % task.workflow.name
        meta_url = 'http://127.0.0.1/api/v1/catalog/stacks/%s/env' % task.workflow.stack_id
        workflow_id = task.workflow.name
        stack_id = task.workflow.stack_id
        user_id = task.workflow.ctx['user_id']

        # Change state (ready -> running)
        p_mgr = self.locator.getManager('PackageManager')
        state = {'workflow_state':{task.get_description():'running'}}
        p_mgr.addEnv2(stack_id, state)

        mgr = self.locator.getManager('WorkflowManager')
        task_info = mgr.getTaskByName(workflow_id, task.get_description())
        self.logger.debug(task_info.output['task_uri'])
        ttype = task_info.output['task_type']
        (cmd_type, group) = self._parseTaskType(ttype)
        self.logger.debug("Task cmd type:%s" % cmd_type)
        self.logger.debug("Task group:%s" % group)
        if group == 'localhost' and cmd_type == 'jeju':
            # every jeju has 'METADATA' keyword for metadata put/get
            kv = "METADATA=%s," % meta_url
            self.logger.debug(stack_id)
            # Add Global Env
            kv = self._getKV(stack_id, 'jeju', kv)
            self.logger.debug(kv)
            if kv[-1] == ",":
                kv = kv[:-1]
            self.logger.debug(kv)
            cmd = '/usr/local/bin/jeju -m %s -k %s' % (task_info.output['task_uri'], kv)
            self.logger.debug('[%s] cmd: %s' % (group, cmd)) 
            os.system(cmd)

        # WARN: group is list
        elif group[0] == '${ZONE_ID}' and cmd_type == 'docker-compose':
            # Call DockerCompose Driver at proper zone
            dcMgr = self.locator.getManager('DockerComposeDriver')
            template = task_info.output['task_uri']
            self.logger.debug("Compose template:%s" % template)
            kv = self._getKV2(stack_id, 'compose')
            self.logger.debug(kv)
            self.logger.debug("user_id:%s" % user_id)
            dcMgr.run(template, {'docker-compose':kv}, stack_id, task.workflow.ctx)

            #env_str = self._getKV2(stack_id, 'docker-compose')
            #self.logger.debug("docker-env:%s" % env_str)
            #dcMgr.execute()

        else:
            # group is list
            for each_group in group:
                group_info = self._getKV2(stack_id, each_group)
                mgr = self.locator.getManager('CloudManager')
                for server_id in group_info:
                    self.logger.debug("Execute@(%s)" % server_id)
                    if cmd_type == 'jeju':
                        kv = 'METADATA=%s,' % meta_url
                        # Add Global Env
                        kv = self._getKV(stack_id, 'jeju', kv)
                        # Add Local Env
                        kv = self._getKV(stack_id, server_id, kv)
                        # Filter last character
                        if kv[-1]==",":
                            kv = kv[:-1]
                        cmd = '/usr/local/bin/jeju -m %s -k %s' % (task_info.output['task_uri'], kv)
                    elif cmd_type == 'ssh':
                        cmd = task_info.output['task_uri']

                    # servers/{server_id}/cmd API
                    params = {'server_id':server_id, 'cmd':cmd}
                    self.logger.debug('[%s] cmd: %s' % (server_id, cmd))
                    output = mgr.executeCmd(params) 
                    self.logger.debug('cmd output:%s' % output)

        # Change State (running->complte)
        state = {'workflow_state':{task.get_description():'complete'}}
        self.logger.debug("Update State to complete:%s" % state)
        p_mgr.addEnv2(stack_id, state)

    def _parseTaskType(self, ttype):
        """
        @params:
            - ttask: task type string (cmd type + node group)
                ex) jeju, jeju+cluster, ssh+node1 ...
                ex) jeju+cluster1,cluster2
        @return: (cmd type, node group)
        """
        items = ttype.split("@")
        if len(items) >= 2:
            glist = items[1].split(",")
            return(items[0], glist)
        else:
            nodes = 'localhost'
            return (items[0] , nodes)

    def _getNodeGroup(self, stack_id, group_name):
        """
        @return: list of server information for ssh connection 
                [{'name':'xxxx','ipv4':'xxxx','floatingip':'xxxx','id':'root','pw':'123456'},
                }
        """
        group_list = self._getKV2(stack_id, group_name)
        if group_list == None:
            # Error
            pass
        mgr = self.locator.getManager('CloudManager')
        infos = []
        for server_id in group_list:
            server_info = mgr.getServerInfo(server_id)
            infos.append(server_info)
        return infos
        
    def _getKV(self, stack_id, key, output):
        """
        @params:
            - stack_id: stack_id
            - key: key for get item
            - output: return string

        get Jeju environment
        @return: string for jeju environment
         ex) "NUM_NODES=3,KV=http://1.2.3.4"
        """
        items = self._getKV2(stack_id, key)
        self.logger.debug(items)
        self.logger.debug(output)
        self.logger.debug(type(items))
        self.logger.debug(items.keys())
        #TODO: items are dictionary
        for key in items.keys():
            self.logger.debug(key)
            self.logger.debug(items[key])
            value = items[key]
            output = output + "%s=%s," % (key, value)
        self.logger.debug(output)
        return output


    def _getKV2(self, stack_id, key):
        """
        @params:
            - stack_id: stack_id
            - key: key for get item
        @ return: value
        """
        mgr = self.locator.getManager('PackageManager')
        return mgr.getEnv2(stack_id, key)

class ServiceTaskParser(TaskParser):
    pass


class CloudBpmnParser(BpmnParser):
    OVERRIDE_PARSER_CLASSES = {
        full_tag('serviceTask') :   (ServiceTaskParser, ServiceTask),
    }


class InMemoryPackager(Packager):
    PARSER_CLASS = CloudBpmnParser

    @classmethod
    def package_in_memory(cls, workflow_name, workflow_files, editor='signavio'):
        s = BytesIO()
        p = cls(s, workflow_name, meta_data=[], editor=editor)
        p.add_bpmn_files_by_glob(workflow_files)
        p.create_package()
        return s.getvalue()

class Node(object):
    """
    Keep the Task information
    """
    def __init__(self, task):
        self.input = {}
        self.output = {}
        self.task = None
        self.task_type = None
        self.task_name = None
        self.description = None
        self.activity = None
        self.init_task(task)

    def init_task(self, task):
        self.task = task
        self.task_type = task.task_spec.__class__.__name__
        self.task_name = task.get_name()
        self.description = task.get_description()
        self.activity = getattr(task.task_spec, 'service_class', '')

    def show(self):
        print "task type:%s" % self.task_type
        print "task name:%s" % self.task_name
        print "description:%s" % self.description
        print "activity :%s" % self.activity
        print "state name:%s" % self.task.get_state_name()
        print "\n"

class BpmnDriver(Manager):

    GLOBAL_CONF = config.getGlobalConfig()

    def set_up(self, path, name, workflow_id, stack_id, ctx):
        self.spec = self.load_spec(path, name)
        self.workflow = PyengineBpmnWorkflow(self.spec, workflow_id=workflow_id, stack_id=stack_id, ctx=ctx)
        #self.run_engine()

    def create_workflow(self):
        self.workflow_spec = self.load_workflow_spec()

    def load_spec(self, content_path, workflow_name):
        return self.load_workflow_spec(content_path, workflow_name)

    def load_workflow_spec(self, content_path, workflow_name):
        package = InMemoryPackager.package_in_memory(workflow_name, content_path)
        return BpmnSerializer().deserialize_workflow_spec(package)

    def run_engine(self):
        while 1:
            tasks = self.workflow.get_tasks(state=Task.READY)
            print len(tasks)
            if len(tasks) == 0:
                break
            for task in tasks:
                current_node = Node(task)
                current_node.show()
                self.workflow.complete_task_from_id(task.id)

            self.workflow.do_engine_steps()

    def run(self, template, env, stack_id, ctx):
        """
        @params:
            -template:
            -env:
            -stack_id:
        """
        # 1. Load content
        self.env = env
        job = self._loadURI(template)
        self.logger.debug("JOB:\n%s" % job)
        # Save as working directory
        new_path = self._saveTemp(job)
        # Get workflow_id
        w_mgr = self.locator.getManager('WorkflowManager')
        workflow_id  = w_mgr.getWorkflowId(template, template_type = 'bpmn') 
        self.logger.debug("### Workflow ID:%s" % workflow_id)

        # TODO: update name from server
        wf_name = 'Process_1'
        self.set_up(new_path, wf_name, workflow_id, stack_id, ctx)

        # 2. Run BPMN
        self.run_engine()

        # 3. Update Stack state
        p_mgr = self.locator.getManager('PackageManager')
        p_mgr.updateStackState(stack_id, "running")
   
    def _loadURI(self, uri):
        """
        @param:
            - uri
        @return:
            - content

        load content from uri
        """
        r = requests.get(uri)
        if r.status_code == 200:
            return r.text
        raise ERROR_NOT_FOUND(key='template', value=uri)

    def _saveTemp(self, content):
        """
        @param:
            - content
        @return:
            - saved file path
        """
        SAVE_DIR = '/tmp'
        new_path = '%s/%s' % (SAVE_DIR, uuid.uuid4())
        fp = open(new_path, 'w')
        fp.write(content)
        fp.close()
        return new_path
