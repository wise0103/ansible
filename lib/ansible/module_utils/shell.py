#
# (c) 2015 Peter Sprygada, <psprygada@ansible.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#
import os
import re
import socket
import time

# py2 vs py3; replace with six via ansiballz
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO

try:
    import paramiko
    from paramiko.ssh_exception import AuthenticationException
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False

from ansible.module_utils.basic import get_exception
from ansible.module_utils.network import NetworkError

ANSI_RE = [
    re.compile(r'(\x1b\[\?1h\x1b=)'),
    re.compile(r'\x08.')
]

def to_list(val):
    if isinstance(val, (list, tuple)):
        return list(val)
    elif val is not None:
        return [val]
    else:
        return list()


class ShellError(Exception):

    def __init__(self, msg, command=None):
        super(ShellError, self).__init__(msg)
        self.message = msg
        self.command = command


class Shell(object):

    def __init__(self, prompts_re=None, errors_re=None, kickstart=True):
        self.ssh = None
        self.shell = None

        self.kickstart = kickstart
        self._matched_prompt = None

        self.prompts = prompts_re or list()
        self.errors = errors_re or list()

    def open(self, host, port=22, username=None, password=None, timeout=10,
             key_filename=None, pkey=None, look_for_keys=None,
             allow_agent=False, key_policy="loose", proxy_command=None):

        self.ssh = paramiko.SSHClient()
        if key_policy != "ignore":
            self.ssh.load_system_host_keys()
            try:
                self.ssh.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
            except IOError:
                pass

        if key_policy == "strict":
            self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # unless explicitly set, disable look for keys if a password is
        # present. this changes the default search order paramiko implements
        if not look_for_keys:
            look_for_keys = password is None

        sock_kwarg = {}
        if proxy_command:
            replacers = {
                '%h': host,
                '%p': port,
                '%r': username
            }
            for find, replace in replacers.items():
                proxy_command = proxy_command.replace(find, str(replace))
            try:
                sock_kwarg = {'sock': paramiko.ProxyCommand(proxy_command)}
            except AttributeError:
                raise ShellError('Paramiko ProxyCommand support unavailable. '
                                 'Please upgrade to Paramiko 1.9.0 or newer. '
                                 'Not using configured ProxyCommand')
                                 
        try:
            self.ssh.connect(
                host, port=port, username=username, password=password,
                timeout=timeout, look_for_keys=look_for_keys, pkey=pkey,
                key_filename=key_filename, allow_agent=allow_agent,
                **sock_kwarg
            )

            self.shell = self.ssh.invoke_shell()
            self.shell.settimeout(timeout)
        except socket.gaierror:
            raise ShellError("unable to resolve host name")
        except AuthenticationException:
            raise ShellError('Unable to authenticate to remote device')

        if self.kickstart:
            self.shell.sendall("\n")

        self.receive()

    def strip(self, data):
        for regex in ANSI_RE:
            data = regex.sub('', data)
        return data

    def receive(self, cmd=None):
        recv = StringIO()
        handled = False

        while True:
            data = self.shell.recv(200)

            recv.write(data)
            recv.seek(recv.tell() - 200)

            window = self.strip(recv.read())

            if hasattr(cmd, 'prompt') and not handled:
                prompt_matched = self.handle_prompt(window, prompt=cmd.prompt, response=cmd.response)
                if prompt_matched:
                    if isinstance(cmd.prompt, (list, tuple)):
                        index = cmd.prompt.index(prompt_matched)
                        del cmd.response[index]
                        del cmd.prompt[index]
                        if cmd.prompt == [] or index >= len(cmd.prompt):
                            handled = True
                    else:
                        handled = True
                    if handled:
                        time.sleep(cmd.delay)
                        if cmd.is_reboot:
                            resp = self.strip(recv.getvalue())
                            return self.sanitize(cmd, resp)

            try:
                if self.find_prompt(window):
                    resp = self.strip(recv.getvalue())
                    return self.sanitize(cmd, resp)
            except ShellError:
                exc = get_exception()
                exc.command = cmd
                raise

    def send(self, commands):
        responses = list()
        try:
            for command in to_list(commands):
                cmd = '%s\r' % str(command)
                self.shell.sendall(cmd)
                responses.append(self.receive(command))
        except socket.timeout:
            raise ShellError("timeout trying to send command: %s" % cmd)
        except socket.error:
            exc = get_exception()
            raise ShellError("problem sending command to host: %s" % exc.message)
        return responses

    def close(self):
        self.shell.close()

    def handle_prompt(self, resp, prompt, response):
        if not prompt or not response:
            return

        prompt = to_list(prompt)
        response = to_list(response)

        for pr, ans in zip(prompt, response):
            match = pr.search(resp)
            if match:
                cmd = '%s\r' % ans
                self.shell.sendall(cmd)
                return pr

    def sanitize(self, cmd, resp):
        cleaned = []
        for line in resp.splitlines():
            if line.startswith(str(cmd)) or self.find_prompt(line):
                continue
            cleaned.append(line)
        return "\n".join(cleaned)

    def find_prompt(self, response):
        for regex in self.errors:
            if regex.search(response):
                raise ShellError('matched error in response: %s' % response)

        for regex in self.prompts:
            match = regex.search(response)
            if match:
                self._matched_prompt = match.group()
                return True


class CliBase(object):
    """Basic paramiko-based ssh transport any NetworkModule can use."""

    def __init__(self):
        if not HAS_PARAMIKO:
            raise NetworkError(
                msg='paramiko is required but does not appear to be installed.  '
                'It can be installed using  `pip install paramiko`'
            )

        self.shell = None
        self._connected = False
        self.default_output = 'text'

    def connect(self, params, kickstart=True, **kwargs):
        host = params['host']
        port = params.get('port') or 22

        username = params['username']
        password = params.get('password')
        key_file = params.get('ssh_keyfile')
        timeout = params['timeout']
        
        proxy_command = params['proxy_command']

        try:
            self.shell = Shell(
                kickstart=kickstart,
                prompts_re=self.CLI_PROMPTS_RE,
                errors_re=self.CLI_ERRORS_RE,
            )
            self.shell.open(
                host, port=port, username=username, password=password,
                key_filename=key_file, timeout=timeout,
                proxy_command=proxy_command
            )
        except ShellError:
            exc = get_exception()
            raise NetworkError(
                msg='failed to connect to %s:%s' % (host, port), exc=str(exc)
            )

        self._connected = True

    def disconnect(self, **kwargs):
        self.shell.close()
        self._connected = False

    def authorize(self, params, **kwargs):
        pass

    def initialize(self, params, **kwargs):
        pass

    ### Command methods ###

    def execute(self, commands, **kwargs):
        try:
            return self.shell.send(commands)
        except ShellError:
            exc = get_exception()
            raise NetworkError(exc.message, commands=commands)

    def run_commands(self, commands, **kwargs):
        return self.execute(to_list(commands))

    ### Config methods ###

    def load_config(self, commands, **kwargs):
        raise NotImplementedError

    def replace_config(self, commands, **kwargs):
        raise NotImplementedError

    def save_config(self):
        raise NotImplementedError
