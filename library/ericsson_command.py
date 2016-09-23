#!/usr/bin/python

import re
import time

from ansible.module_utils.basic import get_exception
from ansible.module_utils.netcli import CommandRunner
from ansible.module_utils.netcli import AddCommandError, FailedConditionsError
from ansible.module_utils.network import NetworkModule, NetworkError

from ansible.module_utils.ericsson.cli import *


VALID_KEYS = ['command', 'output', 'prompt', 'response', 'is_reboot', 'delay']

def to_lines(stdout):
    for item in stdout:
        if isinstance(item, basestring):
            item = str(item).split('\n')
        yield item

def parse_commands(module):

    for cmd in module.params['commands']:
        if isinstance(cmd, basestring):
            cmd = dict(command=cmd, output=None)
        elif 'command' not in cmd:
            module.fail_json(msg='command keyword argument is required')
        elif cmd.get('output') not in [None, 'text', 'json']:
            module.fail_json(msg='invalid output specified for command')
        elif not set(cmd.keys()).issubset(VALID_KEYS):
            module.fail_json(msg='unknown keyword specified')        
        yield cmd

def main():
    spec = dict(
        type=dict(required=True),

        commands=dict(type='list', required=True),

        wait_for=dict(type='list', aliases=['waitfor']),
        match=dict(default='all', choices=['all', 'any']),

        retries=dict(default=10, type='int'),
        interval=dict(default=1, type='int')
    )

    module = NetworkModule(argument_spec=spec,
                           connect_on_load=False,
                           supports_check_mode=True)

    commands = list(parse_commands(module))
    conditionals = module.params['wait_for'] or list()

    warnings = list()

    runner = CommandRunner(module)

    for cmd in commands:
        if module.check_mode and not cmd['command'].startswith('show'):
            warnings.append('only show commands are supported when using '
                            'check mode, not executing `%s`' % cmd['command'])
        else:
            if cmd['command'].startswith('conf'):
                module.fail_json(msg='ios_command does not support running '
                                     'config mode commands.  Please use '
                                     'ios_config instead')
            try:
                runner.add_command(**cmd)
            except AddCommandError:
                exc = get_exception()
                warnings.append('duplicate command detected: %s' % cmd)

    for item in conditionals:
        runner.add_conditional(item)

    runner.retries = module.params['retries']
    runner.interval = module.params['interval']
    runner.match = module.params['match']

    try:
        runner.run()
    except FailedConditionsError:
        exc = get_exception()
        module.fail_json(msg=str(exc), failed_conditions=exc.failed_conditions)
    except NetworkError:
        exc = get_exception()
        module.fail_json(msg=str(exc))

    result = dict(changed=False, stdout=list())

    for cmd in commands:
        try:
            output = runner.get_command(cmd['command'])
        except ValueError:
            output = 'command not executed due to check_mode, see warnings'
        result['stdout'].append(output)

    result['warnings'] = warnings
    result['stdout_lines'] = list(to_lines(result['stdout']))

    module.exit_json(**result)


if __name__ == '__main__':
    main()

