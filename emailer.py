from __future__ import unicode_literals

import envelopes
import envelopes.connstack
from flask import Flask
import flask
import hmac
import json
import logging
import os
import os.path
import rollbar
import rollbar.contrib.flask
import sha
import smptlib
from socket import gaierror

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)


@app.before_first_request
def init_rollbar():
    """Configure rollbar to capture exceptions."""
    if app.config.get('TESTING', False):
        logging.warn(
            'Skipping rollbar init because TESTING flag is set on flask app.')
        return

    rollbar.init(
        # throw KeyError if env var is not set.
        os.environ['ROLLBAR_ACCESS_TOKEN'],
        os.environ.get('GITHUB_COMMIT_EMAILER_ROLLBAR_ENV',
                       'github-email-notifications'),
        root=os.path.dirname(os.path.realpath(__file__)),
        allow_logging_basic_config=False
    )
    flask.got_request_exception.connect(
        rollbar.contrib.flask.report_exception, app)


@app.before_request
def app_before_request():
    envelopes.connstack.push_connection(
        envelopes.SendGridSMTP(
            login=os.environ.get('SENDGRID_USERNAME'),
            password=os.environ.get('SENDGRID_PASSWORD'))
    )


@app.after_request
def app_after_request(response):
    envelopes.connstack.pop_connection()
    return response


@app.route('/')
def index():
    """Redirect to chapel homepage."""
    return flask.redirect('http://chapel-lang.org/', code=301)


@app.route('/commit-email', methods=['POST'])
def commit_email():
    """Receive web hook from github and generate email."""

    # Only look at push events. Ignore the rest.
    event = flask.request.headers['x-github-event']
    logging.info('Received "{0}" event from github.'.format(event))
    if event != 'push':
        logging.info('Skipping "{0}" event.'.format(event))
        return 'nope'

    # Verify signature.
    secret = _get_secret()

    gh_signature = flask.request.headers.get('x-hub-signature', '')
    if not _valid_signature(gh_signature, flask.request.data, secret):
        logging.warn('Invalid signature, skipping request.')
        return 'nope'

    json_dict = flask.request.get_json()
    logging.info('json body: {0}'.format(json_dict))

    if json_dict['deleted']:
        logging.info('Branch was deleted, skipping email.')
        return 'nope'

    added = '\n'.join(map(lambda f: 'A {0}'.format(f),
                          json_dict['head_commit']['added']))
    removed = '\n'.join(map(lambda f: 'R {0}'.format(f),
                            json_dict['head_commit']['removed']))
    modified = '\n'.join(map(lambda f: 'M {0}'.format(f),
                             json_dict['head_commit']['modified']))
    changes = '\n'.join(filter(lambda i: bool(i), [added, removed, modified]))

    pusher_email = '{0} <{1}>'.format(json_dict['pusher']['name'],
                                      json_dict['pusher']['email'])

    msg_info = {
        'repo': json_dict['repository']['full_name'],
        'branch': json_dict['ref'],
        'revision': json_dict['head_commit']['id'][:7],
        'message': json_dict['head_commit']['message'],
        'changed_files': changes,
        'pusher': json_dict['pusher']['name'],
        'pusher_email': pusher_email,
        'compare_url': json_dict['compare'],
    }
    _send_email(msg_info)

    return 'yep'


def _get_secret():
    """Returns secret from environment. Raises ValueError if not set
    in environment."""
    if 'GITHUB_COMMIT_EMAILER_SECRET' not in os.environ:
        logging.error('No secret configured in environment.')
        raise ValueError('No secret configured in environment.')
    return os.environ.get('GITHUB_COMMIT_EMAILER_SECRET')


def _send_email(msg_info):
    """Create and send commit notification email."""
    sender = _get_sender(msg_info['pusher_email'])
    #recipient = os.environ.get('GITHUB_COMMIT_EMAILER_RECIPIENT')

    if sender is None or recipient is None:
        logging.error('sender and recipient config vars must be set.')
        raise ValueError('sender and recipient config vars must be set.')

    recipient_ccs = os.environ.get('GITHUB_COMMIT_EMAILER_RECIPIENT_CC', None)
    if recipient_ccs is not None:
        recipient_cc = recipient_ccs.split(",")
    else:
        recipient_cc = None
    reply_to = os.environ.get('GITHUB_COMMIT_EMAILER_REPLY_TO', None)
    approved = os.environ.get('GITHUB_COMMIT_EMAILER_APPROVED_HEADER', None)
    subject = _get_subject(msg_info['repo'], msg_info['message'])

"""    body = \"""Branch: {branch}
Revision: {revision}
Author: {pusher}
Log Message:

{message}

Modified Files:
{changed_files}

Compare: {compare_url}
\""".format(**msg_info)

    msg = envelopes.Envelope(
        to_addr=recipient,
        from_addr=sender,
        subject=subject,
        text_body=body,
        cc_addr=recipient_cc,
    )

    if reply_to is not None:
        msg.add_header('Reply-To', reply_to)
    if approved is not None:
        msg.add_header('Approved', approved)

    # Disable SendGrid click tracking.
    send_grid_disable_click_tracking = json.dumps(
        {'filters': {'clicktrack': {'settings': {'enable': 0}}}})
    msg.add_header('X-SMTPAPI', send_grid_disable_click_tracking)

    smtp = envelopes.connstack.get_current_connection()
    logging.info('Sending email: {0}'.format(msg))
    smtp.send(msg)"""


    # now you can play with your code. Let’s define the SMTP server separately here:
    port = 2525 
    smtp_server = "smtp.mailtrap.io"
    login = "da9c97e528ece0" # paste your login generated by Mailtrap
    password = "ea9e8ff9628cce" # paste your password generated by Mailtrap

    receiver = "joseph.tursi@hpe.com"
    
    # type your message: use two newlines (\n) to separate the subject from the message body, and use 'f' to  automatically insert variables in the text
    message = f"""\
Subject: Hi Mailtrap
To: {receiver}
From: {sender}

This is my first message with Python."""

    try:
        #send your message with credentials specified above
        with smtplib.SMTP(smtp_server, port) as server:
            server.login(login, password)
            server.sendmail(sender, receiver, message)

        # tell the script to report if your message was sent or which errors need to be fixed 
        print('Sent')
    except (gaierror, ConnectionRefusedError):
        print('Failed to connect to the server. Bad connection settings?')
    except smtplib.SMTPServerDisconnected:
        print('Failed to connect to the server. Wrong user/password?')
    except smtplib.SMTPException as e:
        print('SMTP error occurred: ' + str(e))


def _get_sender(pusher_email):
    """Returns "From" address based on env config and default from."""
    use_author = 'GITHUB_COMMIT_EMAILER_SEND_FROM_AUTHOR' in os.environ
    if use_author:
        sender = pusher_email
    else:
        sender = os.environ.get('GITHUB_COMMIT_EMAILER_SENDER')
    return sender


def _get_subject(repo, message):
    """Returns subject line from repo name and commit message."""
    message_lines = message.splitlines()

    # For github merge commit messages, the first line is "Merged pull request
    # #blah ...", followed by two line breaks. The third line is where the
    # author's commit message starts. So, if a third line is available, use
    # it. Otherwise, just use the first line.
    if len(message_lines) >= 3:
        subject_msg = message_lines[2]
    else:
        subject_msg = message_lines[0]
    subject_msg = subject_msg[:50]
    subject = '[Chapel Merge] {0}'.format(subject_msg)
    return subject


def _valid_signature(gh_signature, body, secret):
    """Returns True if GitHub signature is valid. False, otherwise."""
    def to_str(s):
        if isinstance(s, unicode):
            return str(s)
        else:
            return s

    gh_signature = to_str(gh_signature)
    body = to_str(body)
    secret = to_str(secret)

    expected_hmac = hmac.new(secret, body, sha)
    expected_signature = to_str('sha1=' + expected_hmac.hexdigest())
    return hmac.compare_digest(expected_signature, gh_signature)
