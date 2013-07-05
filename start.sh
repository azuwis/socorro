#!/bin/bash
tmux new-session -d -s socorro
tmux new-window -n collector 'PYTHONPATH=.:thirdparty exec ./socorro-virtualenv/bin/python socorro/collector/collector_app.py'
tmux new-window -n monitor 'PYTHONPATH=.:thirdparty exec ./socorro-virtualenv/bin/python socorro/monitor/monitor_app.py'
tmux new-window -n middleware 'PYTHONPATH=.:thirdparty exec ./socorro-virtualenv/bin/python socorro/middleware/middleware_app.py'
tmux new-window -n processor 'PYTHONPATH=.:thirdparty exec ./socorro-virtualenv/bin/python socorro/processor/processor_app.py'
tmux new-window -n webapp 'cd webapp-django; PYTHONPATH=..:../thirdparty exec ../socorro-virtualenv/bin/python ./manage.py runserver'
