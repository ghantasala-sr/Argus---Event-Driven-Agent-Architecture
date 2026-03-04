#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { MessagingStack } from "../lib/stacks/messaging-stack";
import { StorageStack } from "../lib/stacks/storage-stack";
import { ApiStack } from "../lib/stacks/api-stack";
import { AgentsStack } from "../lib/stacks/agents-stack";

const app = new cdk.App();
const stage = app.node.tryGetContext("stage") || "dev";
const prefix = `Argus-${stage}`;

// --- Phase 1 Stacks ---

const messagingStack = new MessagingStack(app, `${prefix}-Messaging`, {
    stage,
    description: "Argus SNS topics + SQS queues for event-driven agent communication",
});

const storageStack = new StorageStack(app, `${prefix}-Storage`, {
    stage,
    description: "Argus DynamoDB tables + S3 buckets for review data",
});

const apiStack = new ApiStack(app, `${prefix}-Api`, {
    stage,
    prWebhookTopic: messagingStack.prWebhookTopic,
    description: "Argus API Gateway for GitHub webhook ingestion",
});

const agentsStack = new AgentsStack(app, `${prefix}-Agents`, {
    stage,
    parseQueue: messagingStack.parseQueue,
    prParsedTopic: messagingStack.prParsedTopic,
    reviewsTable: storageStack.reviewsTable,
    securityQueue: messagingStack.securityQueue,
    styleQueue: messagingStack.styleQueue,
    performanceQueue: messagingStack.performanceQueue,
    testQueue: messagingStack.testQueue,
    reviewFindingsTopic: messagingStack.reviewFindingsTopic,
    summaryQueue: messagingStack.summaryQueue,
    learnQueue: messagingStack.learnQueue,
    reviewCompleteTopic: messagingStack.reviewCompleteTopic,
    description: "Argus Lambda functions for AI review agents",
});

// Dependency ordering
apiStack.addDependency(messagingStack);
agentsStack.addDependency(messagingStack);
agentsStack.addDependency(storageStack);
