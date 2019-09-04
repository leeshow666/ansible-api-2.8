#! /usr/bin/env python
# @Author: leeshow
# @Date: 2019-09-03 11:48
# @File: ansible_lib.py

import json
import shutil  # 一个简便的文件操作工具
from multiprocessing import cpu_count
from ansible import constants as C  # 用于获取ansible内置的一些常量
from ansible.module_utils.common.collections import ImmutableDict  # 用于自定制一些选项
from ansible import context  # 上下文管理器，他就是用来接收 ImmutableDict 的示例对象
from ansible.parsing.dataloader import DataLoader  # 解析 json/ymal/ini 格式的文件
from ansible.vars.manager import VariableManager  # 管理主机和主机组的变量
from ansible.inventory.manager import InventoryManager  # 管理资产文件（动态资产、静态资产）或者主机列表
from ansible.playbook.play import Play  # 用于执行 Ad-hoc 的核心类，即ansible相关模块，命令行的ansible -m方式
from ansible.executor.task_queue_manager import TaskQueueManager  # ansible 底层用到的任务队列管理器
from ansible.executor.playbook_executor import PlaybookExecutor  # 执行 playbook 的核心类，即命令行的ansible-playbook *.yml
from ansible.errors import AnsibleError  # ansible 自身的一些异常
from ansible.plugins.callback import CallbackBase  # 回调基类，处理ansible的成功失败信息，这部分对于二次开发自定义可以做比较多的自定义


class CallbackModule(CallbackBase):
    """
    重写callbackBase类的部分方法，这里只做了些简单的处理
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.host_ok = {}
        self.host_unreachable = {}
        self.host_failed = {}

    def v2_runner_on_unreachable(self, result):
        self.host_unreachable[result._host.get_name()] = result

    def v2_runner_on_ok(self, result, *args, **kwargs):
        self.host_ok[result._host.get_name()] = result

    def v2_runner_on_failed(self, result, *args, **kwargs):
        self.host_failed[result._host.get_name()] = result


class AnsibleAPI:
    """
    初始化ansible的相关对象及参数
    """

    def __init__(self, check=False, remote_user="root", private_key_file=None, forks=cpu_count, inventory_source=None,
                 extra_vars=None, dynamic_inventory=None):
        """
        可以选择性的针对业务场景在初始化中加入用户定义的参数
        :param check:
        :param remote_user:
        :param private_key_file:
        :param forks:
        :param inventory_source:
        :param extra_vars:
        :param dynamic_inventory:
        """
        # 运行前检查，即命令行的-C
        self.check = check
        # key登陆文件
        self.private_key_file = private_key_file
        # 并发连接数
        self.forks = forks
        # 远端登陆用户
        self.remote_user = remote_user
        # 资产来源，可以是一个配置好的 inventory 文件，也可以是一个含有以 "," 为分割符的字符串
        self.inventory_source = inventory_source
        # 数据解析器
        self.loader = DataLoader()
        # 具体的资产对象，此对象可以用来操作group，host，variable
        self.inventory = InventoryManager(loader=self.loader, sources=self.inventory_source)
        # 必须有此参数，假如通过了公钥信任，可以为空dict
        self.passwords = {}
        # 回调结果
        self.results_callback = CallbackModule()
        # 变量管理器
        self.variable_manager = VariableManager(loader=self.loader, inventory=self.inventory)
        self.variable_manager._extra_vars = extra_vars if extra_vars is not None else {}
        # 自定义选项的初始化
        self.__init_options()
        # 组和主机相关，处理动态资产
        self._hosts = set()
        self.dynamic_inventory = dynamic_inventory if dynamic_inventory is not None and isinstance(dynamic_inventory,
                                                                                                   dict) else {}
        self.__init_dynamic_inventory()
        # 其他

    def __init_options(self):
        """
        自定义选项，不用默认值的话可以加入到__init__的参数中
        :return:
        """
        # constants里面可以找到这些参数，ImmutableDict代替了较老的版本的nametuple的方式
        context.CLIARGS = ImmutableDict(
            connection="smart",
            remote_user=self.remote_user,
            ack_pass=None,
            sudo=True,
            sudo_user="root",
            ask_sudo_pass=False,
            module_path=None,
            become=True,
            become_method="sudo",
            become_user="root",
            check=self.check,
            listhosts=None,
            listtasks=None,
            listtags=None,
            syntax=None,
            diff=True,
            subset=None,
            timeout=10,
            private_key_file=self.private_key_file,
            host_key_checking=False,
            forks=self.forks,
            ssh_common_args='-o StrictHostKeyChecking=no',
            ssh_extra_args='-o StrictHostKeyChecking=no',
            verbosity=0,
            start_at_task=None,
        )

    def __init_dynamic_inventory(self):
        """
        处理动态inventory，可以通过前端表单等方式转化而来，最终动态inventory的格式如下：
        dynamic_inventory = {
            "test02": {
                "hosts": ["192.168.56.112"],
                "vars": {"gvar2": "gtest2"},
                "children": ['test03']
            },
            "test03": {
                "hosts": ["192.168.56.113"],
                "vars": {"gvar3": "gtest3"}
            },
            "_meta": {
                "hostvars": {
                    "192.168.56.111": {"hvar1": "htest1"},
                    "192.168.56.112": {"hvar2": "htest2"}
                }
            }
        }
        需要做如下处理：
        组部分
        1. inventoy添加组:self.inventory.add_group(group)
        2. group添加host:self.inventory.add_host(hostname, group)
        3. group添加组变量:self.inventory._inventory.set_variable(group, k, v)
        4. inventory添加子组:self.inventory.add_group(child_name)
        5. group添加子组:self.inventory._inventory.add_child(group, child_name)
        主机部分
        6. inventory添加host:self.inventory.add_host(host)
        7. host添加主机变量:self.inventory._inventory.set_variable(host, k, got[k])
        :return:
        """

        # dynamic_inventory中的"_meta"获取的主机变量
        data_from_meta = None

        # 提取主机变量并解析组信息
        if len(dynamic_inventory) != 0:
            for group, gdata in dynamic_inventory.items():
                if group == '_meta':
                    if 'hostvars' in gdata:
                        data_from_meta = gdata['hostvars']
                else:
                    self._parse_group(group, gdata)

        if len(self._hosts) != 0:
            for host in self._hosts:
                self.inventory.add_host(host)
                if data_from_meta is None:
                    continue
                got = data_from_meta.get(host, {})
                for k, v in got.items():
                    self.inventory._inventory.set_variable(host, k, v)

    # 解析组信息
    def _parse_group(self, group, data):
        # 返回组名，同group = self.inventory._inventory.add_group(group)，2.8相比老版本添加组相关的不好找，在_inventory里
        group = self.inventory.add_group(group)

        # 感觉这段没啥用？不用这段逻辑应该没啥关系
        if not isinstance(data, dict):
            data = {'hosts': data}
        # is not those subkeys, then simplified syntax, host with vars
        elif not any(k in data for k in ('hosts', 'vars', 'children')):
            data = {'hosts': [group], 'vars': data}

        if 'hosts' in data:
            if not isinstance(data['hosts'], list):
                raise AnsibleError("You defined a group '%s' with bad data for the host list:\n %s" % (group, data))

            for hostname in data['hosts']:
                self._hosts.add(hostname)
                self.inventory.add_host(hostname, group)

        if 'vars' in data:
            if not isinstance(data['vars'], dict):
                raise AnsibleError("You defined a group '%s' with bad data for variables:\n %s" % (group, data))

            for k, v in data['vars'].items():
                # 重点1，给组设置变量
                self.inventory._inventory.set_variable(group, k, v)

        if group != '_meta' and isinstance(data, dict) and 'children' in data:
            for child_name in data['children']:
                child_name = self.inventory.add_group(child_name)
                # 重点2，添加子组
                self.inventory._inventory.add_child(group, child_name)

    def run_playbook(self, playbook_yml):
        playbook = PlaybookExecutor(
            playbooks=[playbook_yml],
            inventory=self.inventory,
            variable_manager=self.variable_manager,
            loader=self.loader,
            passwords=self.passwords,
        )
        playbook._tqm._stdout_callback = self.results_callback
        playbook.run()
        # self.result_row = self.results_callback.result_row

    def run_module(self, module_name, module_args, hosts=None):
        play_source = dict(
            name="Ansible Run Module",
            hosts=hosts,
            gather_facts='no',
            tasks=[
                {"action": {"module": module_name, "args": module_args}},
            ]
        )
        play = Play().load(play_source, variable_manager=self.variable_manager, loader=self.loader)
        tqm = None
        try:
            tqm = TaskQueueManager(
                inventory=self.inventory,
                variable_manager=self.variable_manager,
                loader=self.loader,
                passwords=self.passwords,
                stdout_callback=self.results_callback,
            )
            tqm.run(play)
            # self.result_row = self.results_callback.result_row
        finally:
            if tqm is not None:
                tqm.cleanup()
            # 这个临时目录会在 ~/.ansible/tmp/ 目录下
            shutil.rmtree(C.DEFAULT_LOCAL_TMP, True)

    def get_result(self):
        result_raw = {'success': {}, 'failed': {}, 'unreachable': {}}

        # print(self.results_callback.host_ok)
        for host, result in self.results_callback.host_ok.items():
            result_raw['success'][host] = result._result
        for host, result in self.results_callback.host_failed.items():
            result_raw['failed'][host] = result._result
        for host, result in self.results_callback.host_unreachable.items():
            result_raw['unreachable'][host] = result._result

        # 最终打印结果，并且使用 JSON 继续格式化
        print(json.dumps(result_raw, indent=4))


if __name__ == '__main__':
    # playbook_yml = '/home/ansible/playbook/test.yml'
    private_key_file = '/root/.ssh/id_rsa'
    remote_user = "root"
    extra_vars = {
        "evar2": "test2"
    }
    dynamic_inventory = {
        "test01": {
            "hosts": ["192.168.56.111"],
            "vars": {"gvar1": "gtest1"},
            "children": ['test02']
        },
        "test02": {
            "hosts": ["192.168.56.112"],
            "vars": {"gvar2": "gtest2"}
        },
        "test03": {
            "hosts": ["192.168.56.113"],
            "vars": {"gvar3": "gtest3"}
        },
        "_meta": {
            "hostvars": {
                "192.168.56.111": {
                    "hvar1": "htest1",
                    "gvar1": "ghtest1",
                },
                "192.168.56.112": {
                    "hvar2": "htest2"
                }
            }
        }
    }

    ansible_api = AnsibleAPI(
        inventory_source=None,
        private_key_file=private_key_file,
        extra_vars=extra_vars,
        remote_user=remote_user,
        dynamic_inventory=dynamic_inventory
    )
    # ansible_api.run_playbook(playbook_yml=playbook_yml)
    
    ansible_api.run_module(module_name="shell", module_args="echo {{gvar1}}", hosts="test01")
    
    # print(ansible_api.inventory.hosts.get("192.168.56.112").get_vars())
    # print(ansible_api.variable_manager.extra_vars)
    # print(ansible_api.inventory.groups.get("test01").get_vars())
    # print(ansible_api.inventory.groups.get("test01").get_descendants())  # 子组
    # print(ansible_api.inventory.groups.get("test02").get_ancestors())  # 父组
    # print(ansible_api.inventory.get_groups_dict())
    # print(ansible_api.inventory.list_hosts(pattern="test01"))

    ansible_api.get_result()
