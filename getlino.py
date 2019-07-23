#!python
# Copyright 2019 Rumma & Ko Ltd
# License: BSD (see file COPYING for details)
#

import os
import sys
import stat
import shutil
import grp
import configparser
import subprocess
import virtualenv
import click
import collections
from cookiecutter.main import cookiecutter

from os.path import join

DbEngine = collections.namedtuple(
    'DbEngine', ('name', 'apt_packages', 'python_packages'))
KnownApp = collections.namedtuple(
    'KnownApp', ('name', 'settings_module', 'git_repo'))

# currently getlino supports only nginx, maybe we might add other web servers
USE_NGINX = True
SITES_AVAILABLE = '/etc/nginx/sites-available'
SITES_ENABLED = '/etc/nginx/sites-enabled'

COOKIECUTTER_URL = "https://github.com/lino-framework/cookiecutter-startsite"
BATCH_HELP = "Whether to run in batch mode, i.e. without asking any questions.  "\
             "Don't use this on a machine that is already being used."

LIBREOFFICE_SUPERVISOR_CONF = """
[program:libreoffice]
command = libreoffice --accept="socket,host=127.0.0.1,port=8100;urp;" --nologo --headless --nofirststartwizard
user = root
"""

UWSGI_SUPERVISOR_CONF = """
[program:{}-uwsgi]
command = /usr/local/bin/uwsgi --ini {}/nginx/{}-uwsgi.ini
"""

DB_ENGINES = [
    DbEngine('pgsql', "postgresql postgresql-contrib", "psycopg2-binary"),
    DbEngine(
        'mysql', "mysql-server libmysqlclient-dev python-dev libffi-dev libssl-dev python-mysqldb", "mysqlclient"),
    DbEngine('sqlite', "", "")
]

KNOWN_APPS = [
    KnownApp("noi", "lino_noi.lib.noi.settings",
             "https://github.com/lino-framework/noi"),
    KnownApp("voga", "lino_voga.lib.voga.settings",
             "https://github.com/lino-framework/voga"),
    KnownApp("cosi", "lino_cosi.lib.cosi.settings",
             "https://github.com/lino-framework/cosi"),
    KnownApp("avanti", "lino_avanti.lib.avanti.settings",
             "https://github.com/lino-framework/avanti"),
    KnownApp("weleup", "lino_weleup.lib.weleup.settings",
             "https://github.com/lino-framework/weleup"),
    KnownApp("welcht", "lino_voga.lib.voga.settings",
             "https://github.com/lino-framework/welcht"),
    KnownApp("min2", "lino_book.projects.min2.settings",
             "https://github.com/lino-framework/book"),
]

APPNAMES = [a.name for a in KNOWN_APPS]

CONF_FILES = ['/etc/getlino/getlino.conf',
              os.path.expanduser('~/.getlino.conf')]
CONFIG = configparser.ConfigParser()
FOUND_CONFIG_FILES = CONFIG.read(CONF_FILES)
DEFAULTSECTION = CONFIG[CONFIG.default_section]


CONFIGURE_OPTIONS = []


def add(spec, default, help, type=None):

    kwargs = dict()
    kwargs.update(help=help)
    if type is not None:
        kwargs.update(type=type)
    o = click.Option([spec], **kwargs)
    o.default = DEFAULTSECTION.get(o.name, default)
    CONFIGURE_OPTIONS.append(o)


# must be same order as in signature of configure command below
add('--projects-root', '/usr/local/lino', 'Base directory for Lino sites')
add('--backups-root', '/var/backups/lino', 'Base directory for backups')
add('--log-root', '/var/log/lino', 'Base directory for log files')
add('--usergroup', 'www-data', "User group for files to be shared with the web server")
add('--supervisor-dir', '/etc/supervisor/conf.d',
    "Directory for supervisor config files")
add('--db-engine', 'sqlite', "Default database engine for new sites.",
    click.Choice([e.name for e in DB_ENGINES]))
add('--env-dir', 'env', "link to virtualenv (relative to project dir)")
add('--repos-dir', 'repositories', "link to repositories (relative to virtualenv)")
add('--appy/--no-appy', True, "Whether this server provides appypod and LibreOffice")
add('--redis/--no-redis', True, "Whether this server provides redis")
add('--devtools/--no-devtools', False,
    "Whether this server provides developer tools (build docs and run tests)")
add('--admin-name', 'Joe Dow', "The full name of the server maintainer")
add('--admin-email', 'joe@example.com',
    "The email address of the server maintainer")

class Installer(object):
    def __init__(self, batch=False):
        self.batch = batch
        self._services = set()
        self._system_packages = set()

    def check_overwrite(self, pth):
        if os.path.exists(pth):
            if os.path.isdir(pth):
                if self.yes_or_no("Overwrite existing directory {} ? [y or n]".format(pth)):
                    shutil.rmtree(pth)
                else:
                    raise click.Abort()
            else:
                if self.yes_or_no("Overwrite existing file {} ? [y or n]".format(pth)):
                    os.remove(pth)
                else:
                    raise click.Abort()

    def yes_or_no(self, msg, yes="yY", no="nN"):
        """Ask for confirmation without accepting a mere RETURN."""
        if self.batch:
            return True
        click.echo(msg, nl=False)
        while True:
            c = click.getchar()
            if c in yes:
                click.echo(" Yes")
                return True
            elif c in no:
                click.echo(" No")
                return False

    def must_restart(self, srvname):
        self._services.add(srvname)

    def runcmd(self, cmd, **kw):
        """Run the cmd similar as os.system(), but stop when Ctrl-C."""
        # kw.update(stdout=subprocess.PIPE)
        # kw.update(stderr=subprocess.STDOUT)
        kw.update(shell=True)
        kw.update(universal_newlines=True)
        # subprocess.check_output(cmd, **kw)
        if self.batch or click.confirm("run {}".format(cmd), default=True):
            click.echo(cmd)
            subprocess.run(cmd, **kw)

    def apt_install(self, packages):
        for pkg in packages.split():
            self._system_packages.add(pkg)

    def run_in_env(self, env, cmd):
        """env is the path of the virtualenv"""
        # click.echo(cmd)
        cmd = ". {}/bin/activate && {}".format(env, cmd)
        self.runcmd(cmd)

    def check_permissions(self, pth, executable=False):
        si = os.stat(pth)

        # check whether group owner is what we want
        usergroup = DEFAULTSECTION.get('usergroup')
        if grp.getgrgid(si.st_gid).gr_name != usergroup:
            if self.batch or click.confirm("Set group owner for {}".format(pth),
                                            default=True):
                shutil.chown(pth, group=usergroup)

        # check access permissions
        mode = stat.S_IRGRP | stat.S_IWGRP
        mode |= stat.S_IRUSR | stat.S_IWUSR
        mode |= stat.S_IROTH
        if stat.S_ISDIR(si.st_mode):
            mode |= stat.S_ISGID | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        elif executable:
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        imode = stat.S_IMODE(si.st_mode)
        if imode ^ mode:
            msg = "Set mode for {} from {} to {}".format(
                pth, imode, mode)
            # pth, stat.filemode(imode), stat.filemode(mode))
            if self.batch or click.confirm(msg, default=True):
                os.chmod(pth, mode)

    def write_supervisor_conf(self, filename, content):
        pth = join(DEFAULTSECTION.get('supervisor_dir'), filename)
        self.check_overwrite(pth)
        with open(pth, 'w') as fd:
            fd.write(content)
        self.must_restart('supervisor')

    def setup_database(self, database, user, pwd, db_engine):
        if db_engine == 'mysql':
            def run(cmd):
                self.runcmd('mysql -u root -p -e "{};"'.format(cmd))
            run("create user '{user}'@'localhost' identified by '{pwd}'".format(**locals()))
            run("create database {database} charset 'utf8'".format(**locals()))
            run("grant all PRIVILEGES on {database}.* to '{user}'@'localhost'".format(**locals()))
        elif db_engine == 'pgsql':
            def run(cmd):
                assert '"' not in cmd
                self.runcmd('sudo -u postgres bash -c "psql -c \"{}\";"'.format(cmd))
            run("CREATE USER {user} WITH PASSWORD '{pwd}';".format(**locals()))
            run("CREATE DATABASE {database};".format(**locals()))
            run("GRANT ALL PRIVILEGES ON DATABASE {database} TO {user};".format(**locals()))
        else:
            click.echo("No setup needed for " + db_engine)

    def run_apt_install(self):
        if len(self._system_packages) == 0:
            return
        click.echo("Must install {} system packages: {}".format(
            len(self._system_packages), ' '.join(self._system_packages)))
        cmd = "apt-get install "
        if self.batch:
            cmd += "-y "
        self.runcmd(cmd + ' '.join(self._system_packages))

    def finish(self):
        self.run_apt_install()
        if len(self._services):
            msg = "Restart services {}".format(self._services)
            if self.batch or click.confirm(msg, default=True):
                for srv in self._services:
                    self.runcmd("service {} restart".format(srv))



def check_usergroup(usergroup):
    for gid in os.getgroups():
        if grp.getgrgid(gid).gr_name == usergroup:
            return True
    return False




def install(packages, sys_executable=None):
    if sys_executable:
        command = ". {}/bin/activate".format(sys_executable)
        runcmd(command)
        for package in packages.split(' '):
            subprocess.call(
                ["{}/bin/python".format(sys_executable), "-m", "pip", "install", package])
    else:
        subprocess.call([sys.executable, "-m", "pip", "install", packages])


# This will be decorated below. We cannot use decorators because we define the
# list of options in CONFIGURE_OPTIONS


def configure(ctx, batch,
              projects_root, backups_root, log_root, usergroup,
              supervisor_dir, db_engine, env_dir, repos_dir,
              appy, redis, devtools, admin_name, admin_email):
    """
    Edit and/or create a configuration file and
    set up this machine to become a Lino production server
    according to the configuration file.
    """

    if len(FOUND_CONFIG_FILES) > 1:
        # reconfigure is not yet supported
        raise click.UsageError("Found multiple config files: {}".format(
            FOUND_CONFIG_FILES))

    # write config file. if there is no system-wide file but a user file, write
    # the user file. Otherwise write the system-wide file.
    if len(FOUND_CONFIG_FILES) == 1:
        conffile = FOUND_CONFIG_FILES[0]
        msg = "This will update configuration file {} [y or n] ?"
    else:
        conffile = CONF_FILES[0]
        msg = "This will create configuration file {} [y or n] ?"

    if batch or click.confirm(msg.format(conffile), default=True):
        pth = os.path.dirname(conffile)
        if not os.path.exists(pth):
            os.makedirs(pth, exist_ok=True)

        if not os.access(os.path.dirname(conffile), os.W_OK):
            raise click.ClickException(
                "No write permission for file {}".format(conffile))

        if not os.access(conffile, os.W_OK):
            raise click.ClickException(
                "No write permission for file {}".format(conffile))
    else:
        raise click.Abort()


    # confvars = """projects_root backups_root usergroup
    # db_engine repos_dir supervisor_dir env_dir
    # appy redis devtools admin_name admin_email""".split()

    # conf_values = locals()

    for p in CONFIGURE_OPTIONS:
        k = p.name
        # if k == "batch":
        #     continue
        v = locals()[k]
        if batch:
            CONFIG.set(CONFIG.default_section, k, str(v))
        else:
            msg = "{} ({})".format(k, p.help)
            kwargs = dict(default=v)
            if p.type is not None:
                kwargs.update(type=p.type)
            answer = click.prompt(msg, **kwargs)
            # conf_values[k] = answer
            CONFIG.set(CONFIG.default_section, k, str(answer))

    with open(conffile, 'w') as fd:
        CONFIG.write(fd)
    click.echo("Wrote config file " + conffile)

    if not i.yes_or_no("Okay to start configuring your system? [y or n]"):
        raise click.Abort()

    i = Installer(batch)

    pth = DEFAULTSECTION.get('projects_root')
    if os.path.exists(pth):
        i.check_permissions(pth, batch)
    elif batch or click.confirm("Create projects root directory {}".format(pth), default=True):
        os.makedirs(pth, exist_ok=True)
        i.check_permissions(pth)

    if batch or click.confirm("Upgrade the system", default=True):
        i.runcmd("apt-get update")
        i.runcmd("apt-get upgrade")

    if batch or click.confirm("Install required system packages", default=True):
        i.apt_install(
            "git subversion python3 python3-dev python3-setuptools python3-pip supervisor")
        i.apt_install("nginx")
        i.apt_install("monit")

        if DEFAULTSECTION.get('devtools'):
            i.apt_install("tidy swig graphviz sqlite3")

        if DEFAULTSECTION.get('redis'):
            i.apt_install("redis-server")

        for e in DB_ENGINES:
            if DEFAULTSECTION.get('db_engine') == e.name:
                i.apt_install(e.apt_packages)

        if DEFAULTSECTION.get('db_engine') == 'mysql':
            i.runcmd("sudo mysql_secure_installation")

        if DEFAULTSECTION.get('appy'):
            i.apt_install("libreoffice python3-uno")
            i.write_supervisor_conf('libreoffice.conf', LIBREOFFICE_SUPERVISOR_CONF)


    i.finish()
    click.echo("Lino server setup completed.")

params = [
    click.Option(['--batch/--no-batch'], default=False, help=BATCH_HELP)
] + CONFIGURE_OPTIONS
configure = click.pass_context(configure)
configure = click.Command('configure', callback=configure,
                          params=params, help=configure.__doc__)



@click.command()
@click.argument('appname', metavar="APPNAME", type=click.Choice(APPNAMES))
@click.argument('prjname')
@click.option('--batch/--no-batch', default=False, help=BATCH_HELP)
@click.option('--dev/--no-dev', default=False,
              help="Whether to use development version of the application")
@click.option('--linodev/--no-linodev', default=False,
              help="Whether to use development version of Lino")
@click.option('--shared-env',
              help="Full path of a shared virtualenv to use for this site")
@click.option('--server-url', default='https://myprjname.example.com',
              help="The URL where this site is published")
@click.pass_context
def startsite(ctx, appname, prjname, batch, dev, linodev, shared_env, server_url):
    """
    Create a new Lino site.

    Arguments:

    APPNAME : The application to run on the new site. 

    PRJNAME : The project name for the new site.

    """ # .format(appnames=' '.join(APPNAMES))

    if len(FOUND_CONFIG_FILES) == 0:
        raise click.UsageError(
            "This server is not yet configured. Did you run `sudo getlino.py configure`?")

    i = Installer(batch)

    prjpath = join(DEFAULTSECTION.get('projects_root'), prjname)

    i.check_overwrite(prjpath)

    # if os.path.exists(prjpath):
    #     raise click.UsageError("Project directory {} already exists.".format(prjpath))

    usergroup = DEFAULTSECTION.get('usergroup')

    if check_usergroup(usergroup) or True:
        click.echo("OK you belong to the {0} user group.".format(usergroup))
    else:
        msg = """\
ERROR: you don't belong to the {0} user group.  Maybe you want to run:
sudo adduser `whoami` {0}"""
        raise click.ClickException(msg.format(usergroup))

    projects_root = DEFAULTSECTION.get('projects_root')
    project_dir = join(projects_root, prjname)
    # envdir = join(project_dir, DEFAULTSECTION.get('env_dir'))
    # full_repos_dir = join(envdir, DEFAULTSECTION.get('repos_dir'))
    admin_name = DEFAULTSECTION.get('admin_name')
    admin_email = DEFAULTSECTION.get('admin_email')
    db_user = prjname
    db_password = "1234"  # todo: generate random password
    db_engine = DEFAULTSECTION.get('db_engine')

    click.echo('Creating a new production site into {0} using Lino {1} ...'.format(project_dir, appname))

    if not batch:
        server_url = click.prompt("Server URL ", default=server_url)
        admin_name = click.prompt("Administrator's full name", default=admin_name)
        admin_email = click.prompt("Administrator's full name", default=admin_email)
        db_user = click.prompt("Database user name", default=db_user)
        db_password = click.prompt("Database user password", default=db_password)

    if not i.yes_or_no("OK to create {} ? [y or n]".format(project_dir)):
        raise click.Abort()

    app = KNOWN_APPS[APPNAMES.index(appname)]
    app_package = app.settings_module.split('.')[0]
    repo_nickname = app.git_repo.split('/')[-1]

    extra_context = {
        "prjname": prjname,
        "projects_root": projects_root,
        "repos_dir": DEFAULTSECTION.get('repos_dir'),
        "env_dir": DEFAULTSECTION.get('env_dir'),
        "appname": appname,
        "repo_nickname": repo_nickname,
        "app_package": app_package,
        "app_settings": app.settings_module,
        "use_app_dev": "y" if dev else 'n',
        "use_lino_dev": "y" if linodev else 'n',
        "server_url": server_url,
        "admin_full_name": admin_name,
        "admin_email": admin_email,
        "db_engine": db_engine,
        "db_user": db_user,
        "db_password": db_password,
        "db_name": prjname,
        # "usergroup": usergroup
    }

    os.umask(0o002)

    click.echo("Running cookiecutter {}...".format(COOKIECUTTER_URL))
    cookiecutter(
        COOKIECUTTER_URL,
        no_input=True, extra_context=extra_context, output_dir=projects_root)

    logdir = join(DEFAULTSECTION.get("log_root"), prjname)
    if batch or click.confirm("Setup log directory {}".format(logdir), default=True):
        i.check_overwrite(logdir)
        os.makedirs(logdir, exist_ok=True)
        i.check_permissions(logdir)
        os.symlink(logdir, join(project_dir, 'log'))
        # TODO: add cron logrotate entry

    os.makedirs(join(project_dir, 'media'), exist_ok=True)

    is_new_env = True
    if shared_env:
        if not os.path.exists(shared_env):
            os.makedirs(shared_env, exist_ok=True)
        envdir = join(shared_env, DEFAULTSECTION.get('env_dir'))
        if os.path.exists(envdir):
            is_new_env = False
            msg = "Update shared virtualenv in {}"
        else:
            msg = "Create shared virtualenv in {}"
        static_dir = join(shared_env, 'static')
        if not os.path.exists(static_dir):
            os.makedirs(static_dir, exist_ok=True)
        os.symlink(static_dir, join(project_dir, 'static'))
    else:
        envdir = join(project_dir, DEFAULTSECTION.get('env_dir'))
        msg = "Create local virtualenv in {}"

    full_repos_dir = join(envdir, DEFAULTSECTION.get('repos_dir'))

    if batch or click.confirm(msg.format(envdir), default=True):

        i.batch = True  # remove for debugging

        if is_new_env:
            virtualenv.create_environment(envdir)
            if not os.path.exists(full_repos_dir):
                os.makedirs(full_repos_dir, exist_ok=True)

        if shared_env:
            os.symlink(envdir, join(project_dir, DEFAULTSECTION.get('env_dir')))

        click.echo("Installing Lino and XL to ...".format(envdir))
        os.chdir(full_repos_dir)
        if linodev:
            if not os.path.exists('lino'):
                i.runcmd("git clone --depth 1 -b master https://github.com/lino-framework/lino")
            i.run_in_env(envdir, "pip install -e lino")
            if not os.path.exists('xl'):
                i.runcmd("git clone --depth 1 -b master https://github.com/lino-framework/xl")
            i.run_in_env(envdir, "pip install -e xl")
        else:
            i.run_in_env(envdir, "pip install lino")

        if dev and app.git_repo:
            if not os.path.exists(repo_nickname):
                i.runcmd("git clone --depth 1 -b master {}".format(app.git_repo))
            i.run_in_env(envdir, "pip install -e {}".format(repo_nickname))
        else:
            i.run_in_env(envdir, "pip install {}".format(app_package))

        for e in DB_ENGINES:
            if DEFAULTSECTION.get('db_engine') == e.name:
                i.run_in_env(envdir, "pip install {}".format(e.python_packages))

        if USE_NGINX:
            i.run_in_env(envdir, "pip install -U uwsgi")

        i.batch = batch

    if USE_NGINX:
        if batch or click.confirm("Configure nginx", default=True):
            filename = "{}.conf".format(prjname)
            avpth = join(SITES_AVAILABLE, filename)
            enpth = join(SITES_ENABLED, filename)
            i.check_overwrite(enpth)
            i.check_overwrite(avpth)
            shutil.copyfile(join(project_dir, 'nginx', filename), avpth)
            os.symlink(avpth, enpth)
            i.must_restart("nginx")
            i.write_supervisor_conf('{}-uwsgi.conf'.format(prjname),
                 UWSGI_SUPERVISOR_CONF.format(prjname, project_dir, prjname))

    os.chdir(project_dir)
    i.run_in_env(envdir, "python manage.py configure")
    i.setup_database(prjname, db_user, db_password, db_engine)
    i.run_in_env(envdir, "python manage.py prep --noinput")
    i.run_in_env(envdir, "python manage.py collectstatic --noinput")

    i.finish()


@click.group()
def main():
    pass


main.add_command(configure)
main.add_command(startsite)

if __name__ == '__main__':
    main()
    # main(auto_envvar_prefix='GETLINO')
