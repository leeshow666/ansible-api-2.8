# ansible-api-2.8
基于ansible api 2.8的封装，可以定制化作为api或者web

## Note

最近想着封装一下ansible的模块作为web api方式，提供给前端调用。

前端可以通过表单或者其他资产系统等方式动态的传入host信息，web api端将这些信息组织成dynamic_inventory的方式，格式如下：

```json
 {
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
```

因为用的ansible是2.8的版本，参考了网上的一些文章，没有找到相关的host和group的管理方式，不过提供了一些思路。ansible从1.9->2.0->2.4->2.6->2.8在lib的调用上差距还是蛮大的，个人感觉尽可能保持某个版本就不再变动了，因为功能层面大致差不多。

本项目只包含核心功能的实现

## 动态资产的实现

管理动态资产主要有如下七个方面（伪代码，详情看具体代码实现）：

1. inventoy添加组: `self.inventory.add_group(group)`
2. group添加host: `self.inventory.add_host(hostname, group)`
3. group添加组变量: `self.inventory._inventory.set_variable(group, k, v)`
4. inventory添加子组: `self.inventory.add_group(child_name)`
5. group添加子组: `self.inventory._inventory.add_child(group, child_name)`
6. inventory添加host: `self.inventory.add_host(host)`
7. host添加主机变量: `self.inventory._inventory.set_variable(host, k, v)`

主要的一个坑在于`self.inventory`没有提供组变量和添加子组的方式，组变量和添加子组放在了一个受保护的属性中，也就是`self.inventory._inventory`，而2.4左右的版本关于主机和组的调用会更方便些。另外就是2.8版本是通过`ImmutableDict`来管理`ansible`的选项。整体来看2.8版本相对之前改进还是有一些的，所以做ansible的封装的话也带来lib的调用相对之前版本的改动也要更多

## 主要功能

1. playbook执行
2. ad-hoc执行
3. json方式记录回调结果
4. 动态添加组，主机，变量

## 额外的补充

回调部分自定制功能比较强，通过继承`CallbackBase`或者直接改写`CallbackBase`可以记录日志追溯执行过程，可以封装为web api返回json给调用方，可以加入消息队列再消费像命令行一样实时看到结果

