# "I have permission but can't see the project" on an automation node

A user on an automation node (the deploy target that receives bundles from the design node through the deployer) could not see or manage a deployed project, although he had supposedly been given the permission he needed. This page records how the two permission layers work and where the investigation stopped. It was not resolved, and the UI path for setting project permissions directly on the automation node was never confirmed.

## Two permission layers, and nodes do not share them

1. Instance level. The user must exist on the automation node and be in the right groups there. Existing on the design node means nothing on the automation node. Each node has its own users and groups. User and group definitions do not sync between nodes by themselves.
2. Project level. The user, or a group the user is in, needs a role on that specific project on the automation node.

Project permissions travel with the bundle on deploy, but they only bind if the referenced users and groups already exist on the target node with exactly the same names. If they do not, the entries arrive and silently do nothing.

Adding the user on design and redeploying is not enough on its own. The user (or their group) must exist on the automation node first. That is managed on the automation node itself, in its instance security settings, and has nothing to do with redeploying.

## Where project permissions can be set

Two possibilities were discussed:

* set on the design node and pushed with the next bundle deploy, or
* set directly on the automation node.

The user looked for a way to add users to a project or bundle on the automation node and could not find one. Our working understanding is that on automation nodes project permissions are mainly managed through the bundle (set on design, carried over on deploy) and the automation node UI is deliberately limited for project management. We did not confirm whether an admin sees a Security/Permissions tab on the deployed project there. Check that as an admin before telling anyone it cannot be done.

## Check in this order

1. Does the user exist on the automation node at all? Look in the automation node's own user list, not the design node's.
2. Is the user in a group that the deployed project's permissions reference, on the automation node, with the identical group name?
3. Was the permission granted to the user directly or through a group? Direct user grants are the ones most likely to silently not bind.
4. If permissions were changed on design, has the bundle been redeployed since?
5. As an admin on the automation node, does the deployed project show a Security/Permissions tab? (Open question.)

## What we would standardise

* Group-based project permissions only. Groups with the same names on design and automation nodes keep bundles portable, while per-user entries drift.
* Users and groups arrive on every node through the same identity source (for example AD to LDAP/SSO to DSS groups). Where group assignment to projects is still manual, the nodes drift apart. Automating group sync closes that gap.

This matters more when design and automation nodes sit in separate AWS accounts, with bundles moving between them through the deployer: nothing about identity crosses that boundary on its own.
