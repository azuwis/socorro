#!/bin/bash
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt/ wheezy-pgdg main" >/etc/apt/sources.list.d/psql.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt-get update
sudo apt-get install build-essential subversion libpq-dev python-virtualenv python-dev postgresql-9.2 postgresql-plperl-9.2 postgresql-contrib-9.2 postgresql-server-dev-9.2 rsync python2.6 python2.6-dev libxslt1-dev git-core mercurial
sudo apt-get install libldap2-dev libsasl2-dev
sudo perl -i.bak -lpe 's/^timezone.*=(.*)/timezone = "UTC"/' /etc/postgresql/9.2/main/postgresql.conf
sudo /etc/init.d/postgresql restart
sudo su - postgres -c "createuser -s $USER"
git clone https://github.com/mozilla/socorro
make json_enhancements_pg_extension
psql -f sql/roles.sql postgres
psql -f sql/roles.sql breakpad
PYTHONPATH=. ./socorro-virtualenv/bin/python ./socorro/external/postgresql/setupdb_app.py --dropdb --fakedata --fakedata_days 1 --database_name=breakpad --database_superusername=$USER
PYTHONPATH=. ./socorro-virtualenv/bin/python ./socorro/cron/crontabber.py --job=weekly-reports-partitions --force
make test
make minidump_stackwalk
make webapp-django
crontab crontab
