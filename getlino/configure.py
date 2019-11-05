# Copyright 2019 Rumma & Ko Ltd
# License: BSD (see file COPYING for details)

import os
import sys
import stat
import shutil
import grp
import platform
import configparser
import subprocess
import click
import collections
from contextlib import contextmanager

from os.path import join

from .utils import CONFIG, CONF_FILES, FOUND_CONFIG_FILES, DEFAULTSECTION
from .utils import KNOWN_REPOS, DB_ENGINES, BATCH_HELP, FRONT_ENDS
from .utils import Installer, ifroot

CERTBOT_AUTO_RENEW = """
echo "0 0,12 * * * root python -c 'import random; import time; time.sleep(random.random() * 3600)' && /usr/local/bin/certbot-auto renew" | tee -a /etc/crontab > /dev/null
"""
HEALTHCHECK_SH = """
#!/bin/bash
# generated by getlino
set -e  # exit on error
echo -n "Checking supervisor status: "
supervisorctl status | awk '{if ( $2 != "RUNNING" ) { print "ERROR: " $1 " is not running"; exit 1}}'
echo "... OK"
"""

MONIT_CONF = """
# generated by getlino
check program status with path /usr/local/bin/healthcheck.sh
    if status != 0 then alert
"""

LIBREOFFICE_SUPERVISOR_CONF = """
# generated by getlino
[program:libreoffice]
command = libreoffice --accept="socket,host=127.0.0.1,port=8100;urp;" --nologo --headless --nofirststartwizard
umask = 0002
"""

LOCAL_SETTINGS = """
# generated by getlino
ADMINS = [
  ["{admin_name}", "{admin_email}"]
]
EMAIL_HOST = 'localhost'
SERVER_EMAIL = 'noreply@{server_domain}'
DEFAULT_FROM_EMAIL = 'noreply@{server_domain}'
STATIC_ROOT = 'env/static'
TIME_ZONE = "{time_zone}"
"""

BASH_ALIASES = """
# generated by getlino
alias a='. env/bin/activate'
alias ll='ls -alF'
alias pm='python manage.py'
alias runserver='python manage.py runserver'
function pywhich() {{
  python -c "import $1; print($1.__file__)"
}}
"""
BASH_ALIASES_GO = """
function go() {{
    for BASE in {go_bases}
    do
      if [ -d $BASE/$1 ] ; then
        cd $BASE/$1;
        return;
      fi
    done
    echo Oops: no project $1
    return -1
}}
"""
BASH_ALIASES_DEV = """
alias pp='per_project'
alias ci='inv ci'
"""

# The configure command will be decorated below. We cannot use decorators
# because we define the list of options in CONFIGURE_OPTIONS because we need
# that list also for asking questions using the help text.

CONFIGURE_OPTIONS = []


def add(spec, default, help, type=None, root_only=False):
    """
    :param root_only: if user is not root, do not ask for the user for the choice.

    """
    kwargs = dict()
    kwargs.update(help=help)
    if type is not None:
        kwargs.update(type=type)
    o = click.Option([spec], **kwargs)
    o.root_only = root_only
    o.default = DEFAULTSECTION.get(o.name, default)  # ~/.getlino.conf
    CONFIGURE_OPTIONS.append(o)


def default_sites_base():
    return ifroot('/usr/local/lino', os.path.expanduser('~/lino'))


def default_shared_env():
    return os.environ.get('VIRTUAL_ENV', '')
    # return os.environ.get('VIRTUAL_ENV', '/usr/local/lino/shared/env')


def default_repos_base():
    if default_shared_env():
        return ifroot('/usr/local/lino/repositories', os.path.expanduser('~/lino/repositories'))
    return ''


def default_db_engine():
    return ifroot("mysql", 'sqlite3')


# must be same order as in signature of configure command below
# add('--prod/--no-prod', True, "Whether this is a production server")
add('--sites-base', default_sites_base, 'Base directory for Lino sites on this server')
add('--local-prefix', 'lino_local', "Prefix for for local server-wide importable packages", root_only=True)
add('--shared-env', default_shared_env, "Directory with shared virtualenv")
add('--repos-base', default_repos_base, "Base directory for shared code repositories")
add('--clone/--no-clone', False, "Clone all contributor repositories and install them to the shared-env")
add('--branch', 'master', "The git branch to use for --clone")
add('--webdav/--no-webdav', True, "Whether to enable webdav on new sites", root_only=True)
add('--backups-base', '/var/backups/lino', 'Base directory for backups', root_only=True)
add('--log-base', '/var/log/lino', 'Base directory for log files', root_only=True)
add('--usergroup', 'www-data', "User group for files to be shared with the web server")
add('--supervisor-dir', '/etc/supervisor/conf.d', "Directory for supervisor config files", root_only=True)
add('--env-link', 'env', "link to virtualenv (relative to project dir)")
add('--repos-link', 'repositories', "link to code repositories (relative to virtualenv)")
add('--appy/--no-appy', ifroot, "Whether this server provides appypod and LibreOffice", root_only=True)
add('--redis/--no-redis', ifroot, "Whether this server provides redis", root_only=True)
add('--devtools/--no-devtools', lambda: not ifroot(),
    "Whether to install development tools (build docs and run tests)")
add('--server-domain', 'localhost', "Domain name of this server")
add('--https/--no-https', False, "Whether this server uses secure http", root_only=True)
add('--ldap/--no-ldap', False, "Whether this server works as an LDAP server", root_only=True)
# disable monit by default as it is not included in debian buster.
add('--monit/--no-monit', False, "Whether this server uses monit", root_only=True)
add('--db-engine', default_db_engine, "Default database engine for new sites.",
    click.Choice([e.name for e in DB_ENGINES]))
add('--db-port', '', "Default database port to use for new sites.")
add('--db-host', 'localhost', "Default database host name for new sites.")
add('--db-user', '', "Default database user name for new sites. Leave empty to use the project name.")
add('--db-password', '', "Default database password for new sites. Leave empty to generate a secure password.")
add('--admin-name', 'Joe Dow', "The full name of the server administrator")
add('--admin-email', 'joe@example.com',
    "The email address of the server administrator")
add('--time-zone', 'Europe/Brussels', "The TIME_ZONE to set on new sites")
add('--linod/--no-linod', True, "Whether new sites use linod", root_only=True)
add('--languages', 'en', "The languages to set on new sites")
add('--front-end', 'lino.modlib.extjs', "The front end to use on new sites",
    click.Choice([r.front_end for r in FRONT_ENDS]))


def configure(ctx, batch,
              sites_base, local_prefix, shared_env, repos_base,
              clone, branch, webdav, backups_base, log_base, usergroup,
              supervisor_dir, env_link, repos_link,
              appy, redis, devtools, server_domain, https, ldap, monit,
              db_engine, db_port, db_host,
              db_user, db_password,
              admin_name, admin_email, time_zone,
              linod, languages, front_end):
    """
    Edit and/or create a configuration file and
    configure this machine to become a Lino production server
    according to the configuration file.
    """

    # if len(FOUND_CONFIG_FILES) > 1:
    #     # reconfigure is not yet supported
    #     raise click.UsageError("Found multiple config files: {}".format(
    #         FOUND_CONFIG_FILES))

    i = Installer(batch)

    conffile = ifroot(CONF_FILES[0], CONF_FILES[1])
    click.echo("This will write to configuration file {}".format(conffile))

    # before asking questions check whether we will be able to store them
    pth = os.path.dirname(conffile)
    if not os.path.exists(pth):
        os.makedirs(pth, exist_ok=True)
    if not os.access(pth, os.W_OK):
        raise click.ClickException(
            "No write permission for directory {}".format(pth))

    if os.path.exists(conffile) and not os.access(conffile, os.W_OK):
        raise click.ClickException(
            "No write permission for file {}".format(conffile))

    for p in CONFIGURE_OPTIONS:
        k = p.name
        v = locals()[k]
        if batch:
            CONFIG.set(CONFIG.default_section, k, str(v))

        elif p.root_only and not ifroot():
            continue

        else:
            msg = "- {} ({})".format(k, p.help)
            kwargs = dict(default=v)
            if p.type is not None:
                kwargs.update(type=p.type)
            answer = click.prompt(msg, **kwargs)
            if type(answer) == type("string"):
                answer = answer.rstrip("/")
            # conf_values[k] = answer
            CONFIG.set(CONFIG.default_section, k, str(answer))

    if not i.yes_or_no("Start configuring your system using above options?"):
        raise click.Abort()

    with open(conffile, 'w') as fd:
        CONFIG.write(fd)
    click.echo("Wrote config file " + conffile)

    if ifroot():
        if batch or i.yes_or_no("Upgrade the system?", default=True):
            with i.override_batch(True):
                i.runcmd("apt-get update -y")
                i.runcmd("apt-get upgrade -y")

    i.apt_install(
        "git subversion python3 python3-dev python3-setuptools python3-pip supervisor")
    i.apt_install("libffi-dev libssl-dev")  # maybe needed for weasyprint
    i.apt_install("build-essential")  # maybe needed for installing Python extensions
    i.apt_install("swig")  # required to install eidreader

    if ifroot():
        i.apt_install("nginx uwsgi-plugin-python3")
        i.apt_install("logrotate")
        i.must_restart('nginx')

    if DEFAULTSECTION.getboolean('devtools'):
        i.apt_install("swig graphviz sqlite3")

    if DEFAULTSECTION.getboolean('monit'):
        i.apt_install("monit")

    if DEFAULTSECTION.getboolean('redis'):
        i.apt_install("redis-server")

    for e in DB_ENGINES:
        if DEFAULTSECTION.get('db_engine') == e.name:
            i.apt_install(e.apt_packages)
            if e.service:
                i.must_restart(e.service)

    if DEFAULTSECTION.getboolean('appy'):
        i.apt_install("libreoffice python3-uno")
        i.apt_install("tidy")
        i.must_restart('supervisor')

    if DEFAULTSECTION.getboolean('ldap'):
        i.apt_install("slapd ldap-utils")

    if ifroot() or True:
        for k in ("log_base", "backups_base"):
            pth = DEFAULTSECTION.get(k)
            if not pth:
                print("Strange: {} is empty...".format(k))
                continue
            if not os.path.exists(pth):
                if batch or i.yes_or_no(
                        "Create {} {} ?".format(k, pth), default=True):
                    os.makedirs(pth, exist_ok=True)
            i.check_permissions(pth)

    i.finish()

    go_bases = []

    if clone:
        click.echo("Installing repositories for shared-env...")
        envdir = DEFAULTSECTION.get('shared_env')
        if not envdir:
            raise click.ClickException("Cannot --clone without --shared-env")
        i.check_virtualenv(envdir)

        repos_base = DEFAULTSECTION.get('repos_base')
        if not repos_base:
            repos_base = join(envdir, DEFAULTSECTION.get('repos_link'))
        if not os.path.exists(repos_base):
            if batch or i.yes_or_no(
                    "Create base directory for repositories {} ?".format(repos_base),
                    default=True):
                os.makedirs(repos_base, exist_ok=True)
        i.check_permissions(repos_base)
        os.chdir(repos_base)
        repos = [r for r in KNOWN_REPOS if r.git_repo]
        if batch or i.yes_or_no("Clone repositories to {} ?".format(repos_base), default=True):
            with i.override_batch(True):
                for repo in repos:
                    i.clone_repo(repo)
        if batch or i.yes_or_no("Install cloned repositories to {} ?".format(envdir), default=True):
            with i.override_batch(True):
                for repo in repos:
                    i.install_repo(repo, envdir)
        go_bases.append(repos_base)

    pth = DEFAULTSECTION.get('sites_base')
    if not os.path.exists(pth):
        if batch or i.yes_or_no("Create base directory for sites {} ?".format(pth), default=True):
            os.makedirs(pth, exist_ok=True)
    i.check_permissions(pth)

    local_prefix = DEFAULTSECTION.get('local_prefix')
    pth = join(DEFAULTSECTION.get('sites_base'), local_prefix)
    if os.path.exists(pth):
        i.check_permissions(pth)
    elif batch or i.yes_or_no("Create shared settings package {} ?".format(pth), default=True):
        os.makedirs(pth, exist_ok=True)
        i.check_permissions(pth)
    with i.override_batch(True):
        i.check_permissions(pth)
        i.write_file(join(pth, '__init__.py'), '')
    i.write_file(join(pth, 'settings.py'),
                 LOCAL_SETTINGS.format(**DEFAULTSECTION))
    go_bases.append(pth)

    if not ifroot():
        pth = os.path.expanduser('~/.lino_bash_aliases')
        ctx = dict(DEFAULTSECTION)
        content = BASH_ALIASES.format(**ctx)
        if len(go_bases):
            ctx.update(go_bases=" ".join(go_bases))
            content += BASH_ALIASES_GO.format(**ctx)
        i.write_file(pth, content)
        i.check_permissions(pth)
        click.echo("add ~/.lino_bash_aliases to your bashrc file for some cool bash shortcut commands")

    if ifroot():
        i.write_logrotate_conf(
            'supervisor.conf', '/var/log/supervisor/supervisord.log')

        if DEFAULTSECTION.getboolean('monit'):
            i.write_file('/usr/local/bin/healthcheck.sh', HEALTHCHECK_SH, executable=True)
            i.write_file('/etc/monit/conf.d/lino.conf', MONIT_CONF)
            # seems that monit creates its own logrotate config file
            # i.write_logrotate_conf(
            #     'monit.conf', '/var/log/monit.log')

        if DEFAULTSECTION.getboolean('appy'):
            i.write_supervisor_conf(
                'libreoffice.conf',
                LIBREOFFICE_SUPERVISOR_CONF.format(**DEFAULTSECTION))

        if DEFAULTSECTION.get('db_engine') == 'mysql':
            i.runcmd("mysql_secure_installation")

        if DEFAULTSECTION.getboolean('https'):
            if shutil.which("certbot-auto"):
                click.echo("certbot-auto already installed")
            elif batch or i.yes_or_no("Install certbot-auto?", default=True):
                with i.override_batch(True):
                    i.runcmd("wget https://dl.eff.org/certbot-auto")
                    i.runcmd("mv certbot-auto /usr/local/bin/certbot-auto")
                    i.runcmd("chown root /usr/local/bin/certbot-auto")
                    i.runcmd("chmod 0755 /usr/local/bin/certbot-auto")
                    i.runcmd("certbot-auto -n")
                    i.runcmd("certbot-auto register --agree-tos -m {} -n".format(DEFAULTSECTION.get('admin_email')))
            if batch or i.yes_or_no("Set up automatic certificate renewal?", default=True):
                i.runcmd(CERTBOT_AUTO_RENEW)

        if DEFAULTSECTION.getboolean('ldap'):
            i.runcmd("dpkg-reconfigure slapd")

    click.echo("getlino configure completed.")


params = [
             click.Option(['--batch/--no-batch'], default=False, help=BATCH_HELP),
         ] + CONFIGURE_OPTIONS
configure = click.pass_context(configure)
configure = click.Command('configure', callback=configure,
                          params=params, help=configure.__doc__)
