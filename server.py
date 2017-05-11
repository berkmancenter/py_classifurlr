import classifurlr, classifurlr.theme_status
import json

def app(environ, start_response):
    path = environ['PATH_INFO'].strip(' /').lower()
    if path == 'url':
        session = json.loads(environ['wsgi.input'].read().decode('utf-8'))
        status = '201 Created'
        headers = [('Content-Type', 'application/json')]
        start_response(status, headers)
        c = classifurlr.run(session)
        return [c.as_json().encode('utf-8')]
    elif path == 'theme':
        data = json.loads(environ['wsgi.input'].read().decode('utf-8'))
        theme = data['theme']
        country = data['country_code']
        statuses = data['url_statuses']
        status = '201 Created'
        headers = [('Content-Type', 'application/json')]
        start_response(status, headers)
        c = classifurlr.theme_status.run(theme, country, statuses)
        return [c.as_json().encode('utf-8')]
    else:
        status = '404 Not Found'
        headers = []
        start_response(status, headers)
        return ['']
