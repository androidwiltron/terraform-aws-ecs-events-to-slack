import os
import json
import logging
import http.client

# ---------------------------------------------------------------------------------------------------------------------
# ENVIRONMENTAL VARIABLES
# ---------------------------------------------------------------------------------------------------------------------

# Boolean flag, which determins if the incoming even should be printed to the output.
LOG_EVENTS = os.getenv('LOG_EVENTS', 'False').lower() in ('true', '1', 't')

SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', '')
if SLACK_WEBHOOK_URL == '':
    raise RuntimeError('The required env variable SLACK_WEBHOOK_URL is not set or empty!')

# ---------------------------------------------------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------------------------------------------------

# Input: EventBridge Message detail_type and detail
# Output: mrkdwn text


def ecs_events_parser(detail_type, detail):
    emoji_event_type = {
        'ERROR': ':exclamation:',
        'WARN': ':warning:',
        'INFO': ':information_source:'
    }
    emoji_event_name = {
        'SERVICE_DEPLOYMENT_IN_PROGRESS': ':arrows_counterclockwise:',
        'SERVICE_DEPLOYMENT_COMPLETED': ':white_check_mark:',
        'SERVICE_DEPLOYMENT_FAILED': ':x:'
    }
    emoji_task_status = {
        'PROVISIONING': ':clock1:',
        'PENDING': ':clock6:',
        'ACTIVATING': ':clock11:',
        'RUNNING': ':up:',
        'DEACTIVATING': ':arrow_backward:',
        'STOPPING': ':rewind:',
        'DEPROVISIONING': ':black_left_pointing_double_triangle_with_vertical_bar:',
        'STOPPED': ':black_square_for_stop:'
    }

    if detail_type == 'ECS Deployment State Change':
        result = f'*Event Detail:*' + emoji_event_type.get(detail['eventType'], "") + emoji_event_name.get(detail['eventName'], "") + '\n' + \
                 '• ' + detail['eventType'] + ' - ' + detail['eventName'] + '\n' + \
                 '• Deployment: ' + detail['deploymentId'] + '\n' + \
                 '• Reason: ' + detail['reason']
        return result

    if detail_type == 'ECS Service Action':
        result = f'*Event Detail:*' + emoji_event_type.get(detail['eventType'], "") + emoji_event_name.get(detail['eventName'], "") + '\n' + \
                 '• ' + detail['eventType'] + ' - ' + detail['eventName']
        if 'capacityProviderArns' in detail:
            capacity_providers = ""
            for capacity_provider in detail['capacityProviderArns']:
                try:
                    capacity_providers = capacity_providers + capacity_provider.split(':')[5].split('/')[1] + ", "
                except Exception:
                    logging.warning('Error parsing clusterArn: `{}`'.format(capacity_provider))
                    capacity_providers = capacity_providers + capacity_provider + ", "
            if capacity_providers != "":
                result = result + '\n' + '• Capacity Providers: ' + capacity_providers
        return result

    if detail_type == 'ECS Task State Change':
        container_instance_id = "UNKNOWN"
        if 'containerInstanceArn' in detail:
            try:
                container_instance_id = detail['containerInstanceArn'].split(':')[5].split('/')[2]
            except Exception:
                logging.warning('Error parsing containerInstanceArn: `{}`'.format(detail['containerInstanceArn']))
                container_instance_id = detail['containerInstanceArn']
        try:
            task_definition = detail['taskDefinitionArn'].split(':')[5].split(
                '/')[1] + ":" + detail['taskDefinitionArn'].split(':')[6]
        except Exception:
            logging.warning('Error parsing taskDefinitionArn: `{}`'.format(detail['taskDefinitionArn']))
            task_definition = detail['taskDefinitionArn']
        try:
            task = detail['taskArn'].split(':')[5].split('/')[2]
        except Exception:
            logging.warning('Error parsing taskArn: `{}`'.format(detail['taskArn']))
            task = detail['taskArn']
        result = f'*Event Detail:* ' + '\n' + \
                 '• Task Definition: ' + task_definition + '\n' + \
                 '• Last: ' + detail['lastStatus'] + ' ' + emoji_task_status.get(detail['lastStatus'], "") + '\n' + \
                 '• Desired: ' + detail['desiredStatus'] + ' ' + emoji_task_status.get(detail['desiredStatus'], "")
        if container_instance_id != "UNKNOWN":
            result = result + '\n' + '• Instance ID: ' + container_instance_id
        if detail['lastStatus'] == 'RUNNING':
            if 'healthStatus' in detail:
                result = result + '\n' + '• HealthStatus: ' + detail['healthStatus']
        if detail['lastStatus'] == 'STOPPED':
            if 'stopCode' in detail:
                result = result + '\n' + ':bangbang: Stop Code: ' + detail['stopCode']
            if 'stoppedReason' in detail:
                result = result + '\n' + ':bangbang: Stop Reason: ' + detail['stoppedReason']
        return result

    return f'*Event Detail:* ```{json.dumps(detail, indent=4)}```'


# Input: EventBridge Message
# Output: Slack Message
def event_to_slack_message(event):
    event_id = event['id'] if 'id' in event else None
    detail_type = event['detail-type']
    account = event['account'] if 'account' in event else None
    time = event['time'] if 'time' in event else None
    region = event['region'] if 'region' in event else None
    resources = ""
    for resource in event['resources']:
        try:
            resources = resources + ":dart: " + resource.split(':')[5] + "\n"
        except Exception:
            logging.warning('Error parsing resource: `{}`'.format(resource))
            resources = resources + ":dart: " + resource + "\n"
    detail = event['detail'] if 'detail' in event else None
    known_detail = ecs_events_parser(detail_type, detail)
    blocks = list()
    contexts = list()
    title = f'*{detail_type}*'
    blocks.append(
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': title
            }
        }
    )
    if resources != "":
        blocks.append(
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': "Involved resources \n" + resources
                }
            }
        )
    if detail is not None and known_detail == "":
        blocks.append(
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': f'*Event Detail:* ```{json.dumps(detail, indent=4)}```'
                }
            }
        )
    if known_detail != "":
        blocks.append(
            {
                'type': 'section',
                'text': {
                    'type': 'mrkdwn',
                    'text': known_detail
                }
            }
        )
    contexts.append({
        'type': 'mrkdwn',
        'text': f'Account: {account} Region: {region}'
    })
    contexts.append({
        'type': 'mrkdwn',
        'text': f'Time: {time} UTC Id: {event_id}'
    })
    blocks.append({
        'type': 'context',
        'elements': contexts
    })
    blocks.append({'type': 'divider'})
    return {'blocks': blocks}


# Slack web hook example
# https://hooks.slack.com/services/XXXXXXX/XXXXXXX/XXXXXXXXXX
def post_slack_message(hook_url, message):
    logging.info(f'Sending message: {json.dumps(message)}')
    headers = {'Content-type': 'application/json'}
    connection = http.client.HTTPSConnection('hooks.slack.com')
    connection.request('POST',
                       hook_url.replace('https://hooks.slack.com', ''),
                       json.dumps(message),
                       headers)
    response = connection.getresponse()
    logging.info('Response: {}, message: {}'.format(response.status, response.read().decode()))
    return response.status


# ---------------------------------------------------------------------------------------------------------------------
# LAMBDA HANDLER
# ---------------------------------------------------------------------------------------------------------------------


def lambda_handler(event, context):
    if LOG_EVENTS:
        logging.warning('Event logging enabled: `{}`'.format(json.dumps(event)))

    if event.get("source") != "aws.ecs":
        raise ValueError('The source of the incoming event is not "aws.ecs"')

    slack_message = event_to_slack_message(event)
    response = post_slack_message(SLACK_WEBHOOK_URL, slack_message)
    if response != 200:
        logging.error(
            "Error: received status `{}` using event `{}` and context `{}`".format(response, event,
                                                                                   context))
    return json.dumps({"code": response})


# For local testing
if __name__ == '__main__':
    with open('./test/eventbridge_event.json') as f:
        test_event = json.load(f)
    lambda_handler(test_event, "default_context")
