import re

from ansible.module_utils.shell import CliBase
from ansible.module_utils.network import register_transport, to_list
from ansible.module_utils.netcli import Command


class AdminCli(CliBase):
    NET_PASSWD_RE = re.compile(r"[\r\n]?password: *$", re.I)

    CLI_PROMPTS_RE = [
        re.compile(r"[\r\n]?[\w+\-\.:\/\[\]]+(?:\([^\)]+\)){0,3}(?:>|#) *$"),
        re.compile(r"[\r\n]?\w+\@[\w\-\.]+: *[\w~]+[>#\$] *$")
    ]

    CLI_ERRORS_RE = [
        re.compile(r"% ?Error"),
        re.compile(r"% ?Bad secret"),
        re.compile(r"invalid input", re.I),
        re.compile(r"(?:incomplete|ambiguous) command", re.I),
        re.compile(r"connection timed out", re.I),
        re.compile(r"[^\r\n]+ not found", re.I),
        re.compile(r"'[^']' +returned error code: ?\d+"),
    ]

    def authorize(self, params, **kwargs):
        passwd = params['auth_pass']
        self.run_commands(
            Command('enable', prompt=self.NET_PASSWD_RE, response=passwd)
        )

    def initialize(self, params, **kwargs):
        if params.get('type') == 'ref-pizza':
            self.shell.send('/usr/lib/siara/bin/exec_cli -x')        
        
        if params.get('type') == 'ssrsim_kvm':
            self.shell.send('export TERM=xterm')
            self.shell.send('stty cols 500')
        else:
            self.shell.send('terminal length 0')
            self.shell.send('terminal width 500')

    ### Cli methods ###

    def run_commands(self, commands, **kwargs):
        return self.execute(to_list(commands))
        
AdminCli = register_transport('admin_cli', default=True)(AdminCli)


class ShellCli(AdminCli):

    CLI_PROMPTS_RE = [
        re.compile(r"[\r\n]?[\w+\-\.:\/\[\]]+(?:\([^\)]+\)){0,3}(?:>|#) *$"),
        re.compile(r"[\r\n]?\w+\@[\w\-\.]+: *[\w~]+# *$"),
        re.compile(r"[\r\n]?bash-\d+\.\d+ *\$ *$")
    ]

    def initialize(self, params, **kwargs):
        super(ShellCli, self).initialize(params, **kwargs)
        self.shell.send('start shell')

ShellCli = register_transport('shell_cli', default=True)(ShellCli)

