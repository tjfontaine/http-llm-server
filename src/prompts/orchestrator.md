First, call `create_web_resource` with port 8080. Then, with the returned
`resource_id`, call `update_web_resource_config`. Then, call `start_web_server`
with the same `resource_id`. Finally, after the server is started, call
`destroy_web_resource` with the same `resource_id` to shut it down.
