#!/bin/bash
tmux new-session -d -s socorro
tmux new-window -n collector 'PYTHONPATH=. exec ./socorro-virtualenv/bin/python socorro/collector/collector_app.py'
tmux new-window -n monitor 'PYTHONPATH=. exec ./socorro-virtualenv/bin/python socorro/monitor/monitor_app.py'
tmux new-window -n middleware 'PYTHONPATH=. exec ./socorro-virtualenv/bin/python socorro/middleware/middleware_app.py'
tmux new-window -n processor 'PYTHONPATH=. exec ./socorro-virtualenv/bin/python socorro/processor/processor_app.py'
tmux new-window -n webapp 'cd webapp-django; exec ./virtualenv/bin/python ./manage.py runserver'
