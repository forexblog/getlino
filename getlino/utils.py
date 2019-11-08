#!python
# Copyright 2019 Rumma & Ko Ltd
# License: BSD (see file COPYING for details)

"""Some utilities for getlino.
"""

import os
from os.path import join, expanduser
import stat
import shutil
import grp
import configparser
import subprocess
import click
import platform
import collections
import getpass
from contextlib import contextmanager
import virtualenv
from jinja2 import Environment, PackageLoader

JINJA_ENV = Environment(loader=PackageLoader('getlino', 'templates'))

# currently getlino supports only nginx, maybe we might add other web servers
USE_NGINX = True

BATCH_HELP = "Whether to run in batch mode, i.e. without asking any questions.  "\
             "Don't use this on a machine that is already being used."

# note that we double curly braces because we will run format() on this string:
LOGROTATE_CONF = """
# generated by getlino
{logfile} {{
    weekly
    missingok
    rotate 156
    compress
    delaycompress
    notifempty
    create 660 root {usergroup}
    su root {usergroup}
    sharedscripts
}}
"""


class DbEngine(object):
    name = None  # Note that the DbEngine.name field must match the Django engine name
    service = None
    apt_packages = ''
    python_packages = ''

    def runcmd(self, i, sqlcmd):
        pass

    def setup_database(self, i, database, user):
        click.echo("No setup needed for " + self.name)

    def setup_user(self, i, context):
        click.echo("No need to setup user for " + self.name)

    def after_prep(self, i, context):
        pass

class SQLite(DbEngine):
    name = 'sqlite3'

    def after_prep(self, i, context):
        project_dir = context['project_dir']
        prjname = context['prjname']
        with i.override_batch(True):
            i.check_permissions(os.path.join(project_dir, prjname))



class MySQL(DbEngine):
    name = 'mysql'
    service = 'mysql'
    default_port = "3306"
    packages = "mysql-server libmysqlclient-dev"
    python_packages = "mysqlclient"

    def __init__(self):
        super(MySQL, self).__init__()
        # apt_packages = "mysql-server libmysqlclient-dev"
        # TODO: support different platforms (Debian, Ubuntu, Elementary, ...)
        # apt_packages += " python-dev libffi-dev libssl-dev python-mysqldb"
        if platform.dist()[0].lower() == "debian" and False:
            self.service = 'mariadb'
            self.packages = "mariadb-server libmariadb-dev-compat libmariadb-dev "\
                "python-dev libffi-dev libssl-dev python-mysqldb"

    def run(self, i, sqlcmd):
        return i.runcmd('mysql -u root -p -e "{};"'.format(sqlcmd))

    def setup_user(self, i, context):
        self.run(i, "create user '{db_user}'@'db_host' identified by '{db_password}'".format(**context))

    def setup_database(self, i, database, user):
        self.run(i, "create database {database} charset 'utf8'".format(**locals()))
        self.run(i, "grant all PRIVILEGES on {database}.* to '{user}'@'localhost'".format(**locals()))

class PostgreSQL(DbEngine):
    name = 'postgresql'
    service = 'postgresql'
    # python_packages = "psycopg2"
    python_packages = "psycopg2-binary"
    default_port = "5432"

    def run(self, i, cmd):
        assert '"' not in cmd
        # self.runcmd('sudo -u postgres bash -c "psql -c \\\"{}\\\""'.format(cmd))
        i.runcmd('sudo -u postgres psql -c "{}"'.format(cmd))

    def setup_user(self, i, context):
        self.run(i, "CREATE USER {db_user} WITH PASSWORD '{db_password}';".format(**context))

    def setup_database(self, i, database, user):
        self.run(i, "CREATE DATABASE {database};".format(**locals()))
        self.run(i, "GRANT ALL PRIVILEGES ON DATABASE {database} TO {user};".format(**locals()))


DB_ENGINES = [MySQL(), PostgreSQL(), SQLite()]

# DbEngine = collections.namedtuple(
#     'DbEngine', ('name service apt_packages python_packages default_port'))
# DB_ENGINES = []
# DB_ENGINES.append(
#     DbEngine('postgresql', 'postgresql', "postgresql postgresql-contrib libpq-dev python-dev", "psycopg2", "5432"))
#     # https://pypi.org/project/psycopg2/ : "The psycopg2-binary package is a
#     # practical choice for development and testing but in production it is
#     # advised to use the package built from sources."
#
# mariadb_apt_packages = "mariadb-server libmariadb-dev-compat libmariadb-dev "\
#     "python-dev libffi-dev libssl-dev python-mysqldb"
# # apt_packages = "mysql-server libmysqlclient-dev"
# # TODO: support different platforms (Debian, Ubuntu, Elementary, ...)
# # apt_packages += " python-dev libffi-dev libssl-dev python-mysqldb"
# if platform.dist()[0].lower() == "debian" and False:
#     DB_ENGINES.append(DbEngine('mysql', 'mariadb', mariadb_apt_packages, "mysqlclient", "3306"))
# else:
#     DB_ENGINES.append(DbEngine('mysql', 'mysql', "mysql-server libmysqlclient-dev", "mysqlclient", "3306"))
# DB_ENGINES.append(DbEngine('sqlite3', '', "sqlite3", "", "0"))


Repo = collections.namedtuple(
    'Repo', 'nickname package_name git_repo settings_module front_end')
REPOS_DICT = {}
KNOWN_REPOS = []

def add(nickname, package_name, git_repo='', settings_module='', front_end=''):
    t = Repo(nickname, package_name, git_repo, settings_module, front_end)
    KNOWN_REPOS.append(t)
    REPOS_DICT[t.nickname] = t
    if t.front_end:
        # add an alias because front ends are identified using their full package name
        REPOS_DICT[t.front_end] = t

# some tools to be installed with --clone because they are required for a complete contributor environment:
add("cd", "commondata", "https://github.com/lsaffre/commondata")
add("be", "commondata.be", "https://github.com/lsaffre/commondata-be")
add("ee", "commondata.ee", "https://github.com/lsaffre/commondata-ee")
add("eg", "commondata.eg", "https://github.com/lsaffre/commondata-eg")
add("atelier", "atelier", "https://github.com/lino-framework/atelier")
add("etgen", "etgen", "https://github.com/lino-framework/etgen")
add("eid", "eidreader", "https://github.com/lino-framework/eidreader")

add("lino", "lino", "https://github.com/lino-framework/lino", "", "lino.modlib.extjs")
add("xl", "lino-xl", "https://github.com/lino-framework/xl")
add("welfare", "lino-welfare", "https://github.com/lino-framework/welfare")
add("amici", "lino-amici", "https://github.com/lino-framework/amici", "lino_amici.lib.amici.settings")
add("avanti", "lino-avanti", "https://github.com/lino-framework/avanti", "lino_avanti.lib.avanti.settings")
add("care", "lino-care", "https://github.com/lino-framework/care", "lino_care.lib.care.settings")
add("cosi", "lino-cosi", "https://github.com/lino-framework/cosi", "lino_cosi.lib.cosi.settings")
add("noi", "lino-noi", "https://github.com/lino-framework/noi", "lino_noi.lib.noi.settings")
add("presto", "lino-presto", "https://github.com/lino-framework/presto", "lino_presto.lib.presto.settings")
add("pronto", "lino-pronto", "https://github.com/lino-framework/pronto", "lino_pronto.lib.pronto.settings")
add("tera", "lino-tera", "https://github.com/lino-framework/tera", "lino_tera.lib.tera.settings")
add("vilma", "lino-vilma", "https://github.com/lino-framework/vilma", "lino_vilma.lib.vilma.settings")
add("voga", "lino-voga", "https://github.com/lino-framework/voga", "lino_voga.lib.voga.settings")
add("weleup", "lino-weleup", "https://github.com/lino-framework/weleup", "lino_weleup.settings")
add("welcht", "lino-welcht", "https://github.com/lino-framework/welcht", "lino_welcht.settings")

add("book", "lino-book", "https://github.com/lino-framework/book")
add("react", "lino-react", "https://github.com/lino-framework/react", "", "lino_react.react")
# experimental: an application which has no repo on its own
add("min1", "", "", "lino_book.projects.min1.settings")
add("min2", "", "", "lino_book.projects.min2.settings")
add("polls", "", "", "lino_book.projects.polls.mysite.settings")
add("cosi_ee", "", "", "lino_book.projects.cosi_ee.settings.demo")
add("lydia", "", "", "lino_book.projects.lydia.settings.demo")
add("team", "", "", "lino_book.projects.team.settings.demo")
add("chatter", "", "", "lino_book.projects.chatter.settings")

APPNAMES = [a.nickname for a in KNOWN_REPOS if a.settings_module]
FRONT_ENDS = [a for a in KNOWN_REPOS if a.front_end]

CONF_FILES = ['/etc/getlino/getlino.conf', expanduser('~/.getlino.conf')]
CONFIG = configparser.ConfigParser()
FOUND_CONFIG_FILES = CONFIG.read(CONF_FILES)
DEFAULTSECTION = CONFIG[CONFIG.default_section]

def ifroot(true=True, false=False):
    if os.geteuid() == 0:
        return true
    return false


class Installer(object):
    """Volatile object used by :mod:`getlino.configure` and :mod:`getlino.startsite`.
    """
    def __init__(self, batch=False):
        self.batch = batch
        # self.asroot = ifroot()
        self._services = set()
        self._system_packages = set()
        if ifroot():
            click.echo("Running as root.")

    def check_overwrite(self, pth):
        """If `pth` (directory or file) exists, remove it after asking for confirmation.
        Return False if it exists and user doesn't confirm.
        """
        if not os.path.exists(pth):
            return True
        if os.path.isdir(pth):
            if self.yes_or_no("Overwrite existing directory {} ?".format(pth)):
                shutil.rmtree(pth)
                return True
        else:
            if self.yes_or_no("Overwrite existing file {} ?".format(pth)):
                os.remove(pth)
                return True
        return False

    def yes_or_no(self, msg, yes="yY", no="nN", default=True):
        """Ask for confirmation without accepting a mere RETURN."""
        if self.batch:
            return default
        click.echo(msg + " [y or n]", nl=False)
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
        """Run the cmd similar as os.system(), but stop when Ctrl-C.

        If the subprocess has non-zero return code, we simply stop. We don't use
        check=True because this would add another useless traceback.  The
        subprocess is responsible for reporting the reason of the error.

        """
        # kw.update(stdout=subprocess.PIPE)
        # kw.update(stderr=subprocess.STDOUT)
        kw.update(shell=True)
        kw.update(universal_newlines=True)
        # kw.update(check=True)
        # subprocess.check_output(cmd, **kw)
        if self.batch or self.yes_or_no("run {}".format(cmd), default=True):
            click.echo(cmd)
            cp = subprocess.run(cmd, **kw)
            if cp.returncode != 0:
                # subprocess.run("sudo journalctl -xe", **kw)
                raise click.ClickException(
                "{} ended with return code {}".format(cmd, cp.returncode))

    def apt_install(self, packages):
        for pkg in packages.split():
            # no check for if package is already installed:
            self._system_packages.add(pkg)

    def run_in_env(self, env, cmd):
        """env is the path of the virtualenv"""
        # click.echo(cmd)
        cmd = ". {}/bin/activate && {}".format(env, cmd)
        self.runcmd(cmd)

    def check_permissions(self, pth, executable=False):
        si = os.stat(pth)

        if ifroot():
            # check whether group owner is what we want
            usergroup = DEFAULTSECTION.get('usergroup')
            if grp.getgrgid(si.st_gid).gr_name != usergroup:
                if self.batch or self.yes_or_no("Set group owner for {}".format(pth),
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
            if self.batch or self.yes_or_no(msg, default=True):
                os.chmod(pth, mode)

    @contextmanager
    def override_batch(self, batch):
        old = self.batch
        try:
            self.batch = batch
            yield self
        finally:
            self.batch = old

    def write_file(self, pth, content, **kwargs):
        if self.check_overwrite(pth):
            with open(pth, 'w') as fd:
                fd.write(content)
            with self.override_batch(True):
                self.check_permissions(pth, **kwargs)
            return True

    def write_supervisor_conf(self, filename, content):
        self.write_file(
            join(DEFAULTSECTION.get('supervisor_dir'), filename), content)
        self.must_restart('supervisor')

    def run_apt_install(self):
        if len(self._system_packages) == 0:
            return
        # click.echo("Must install {} system packages: {}".format(
        #     len(self._system_packages), ' '.join(self._system_packages)))
        cmd = "sudo apt-get install "
        if self.batch:
            cmd += "-y "
        self.runcmd(cmd + ' '.join(self._system_packages))

    def make_file_executable(self,file_path):
        """ Make a file executable """
        st = os.stat(file_path)
        os.chmod(file_path,0o775)
        #os.chmod(file_path, st.st_mode | stat.S_IEXEC)

    def check_virtualenv(self, envdir, context):
        pull_sh_path = join(envdir, 'bin', 'pull.sh')
        ok = False
        if os.path.exists(envdir):
            ok = True
            # msg = "Update virtualenv in {}"
            # return self.batch or click.confirm(msg.format(envdir), default=True)
        else:
            msg = "Create virtualenv in {}"
            if self.batch or self.yes_or_no(msg.format(envdir), default=True):
                # create an empty directory and fix permissions
                os.makedirs(envdir)
                self.check_permissions(envdir)
                virtualenv.create_environment(envdir)
                ok = True
        if ok:
            if not os.path.exists(pull_sh_path):
                self.jinja_write(pull_sh_path, **context)
            self.make_file_executable(pull_sh_path)
        return ok

    def clone_repo(self, repo):
        branch = DEFAULTSECTION.get('branch')
        if not os.path.exists(repo.nickname):
            self.runcmd("git clone --depth 1 -b {} {} {}".format(branch, repo.git_repo, repo.nickname))
        else:
            click.echo(
                "No need to clone {} : directory exists.".format(
                    repo.nickname))

    def install_repo(self, repo, env):
        self.run_in_env(env, "pip install -e {}".format(repo.nickname))

    def check_usergroup(self, usergroup):
        if ifroot():
            return
        for gid in os.getgroups():
            if grp.getgrgid(gid).gr_name == usergroup:
                return
        msg = """\
You {0} don't belong to the {1} user group.  Maybe you want to run:
sudo adduser `whoami` {1}"""
        raise click.ClickException(msg.format(getpass.getuser(),usergroup))

    def write_logrotate_conf(self, conffile, logfile):
        ctx = {}
        ctx.update(DEFAULTSECTION)
        ctx.update(logfile=logfile)
        self.write_file(
            '/etc/logrotate.d/' + conffile,
            LOGROTATE_CONF.format(**ctx))


    def jinja_write(self, pth, tplname=None, **context):
        """
        pth : the full path of the file to generate.
        tplname : name of the template file to render.  If tplname is not specified, use the tail of the output file.
        """
        if not self.check_overwrite(pth):
            return False
        if tplname is None:
            head, tplname = os.path.split(pth)
        tpl = JINJA_ENV.get_template(tplname)
        s = tpl.render(**context)
        with open(pth, 'w') as fh:
            fh.write(s)
        return True


    def finish(self):
        if not ifroot() and False:
            if len(self._system_packages):
                click.echo(
                    "Note that the following system packages were not "
                    "installed because you aren't root:\n{}".format(
                        ' '.join(list(self._system_packages))))
            if len(self._services):
                click.echo(
                    "The following system services were not "
                    "restarted because you aren't root:\n{}".format(
                        ' '.join(list(self._services))))
            return

        self.run_apt_install()

        if len(self._services):
            msg = "Restart services {}".format(self._services)
            if self.batch or self.yes_or_no(msg, default=True):
                with self.override_batch(True):
                    for srv in self._services:
                        try:
                            self.runcmd("sudo service {} restart".format(srv))
                        except Exception:
                            try:
                                self.runcmd("sudo /etc/init.d/{}  restart".format(srv))
                            except Exception:
                                continue
