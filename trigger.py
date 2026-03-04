import boto3
import json

payload = {
    "action": "synchronize",
    "number": 1,
    "pull_request": {
        "number": 1,
        "title": "Update support intel",
        "html_url": "https://github.com/ghantasala-sr/Support_Intel/pull/1",
        "diff_url": "https://github.com/ghantasala-sr/Support_Intel/pull/1.diff",
        "head": {"sha": "latest", "ref": "main"},
        "base": {"ref": "main"}
    },
    "repository": {
        "full_name": "ghantasala-sr/Support_Intel",
        "clone_url": "https://github.com/ghantasala-sr/Support_Intel.git"
    },
    "sender": {"login": "ghantasala-sr"},
    "installation": {"id": 61271107}
}

client = boto3.client('sns', region_name='us-east-1')
client.publish(
    TopicArn='arn:aws:sns:us-east-1:219494607505:argus-dev-pr-webhook',
    Message=json.dumps(payload)
)
print("Trigger payload published to SNS!")
