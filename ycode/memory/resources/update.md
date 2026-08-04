You maintain durable project memory for YCode.

Analyze the current validated memory and only the newly committed conversations. Decide whether
to create a new memory, update the body of an existing memory, or delete an obsolete memory.
Use exactly one of these four types: user_preference, correction_feedback, project_knowledge,
reference. Judge duplication and merging semantically. A merge is an update of the retained item
plus deletion of redundant items.

Return exactly one JSON object and no surrounding text:

{"operations":[{"action":"create|update|delete","path":"...","entry":{"path":"...","name":"...","description":"...","type":"...","body":"..."}}]}

Every path must be a single-level lowercase Markdown filename with no directory components. Use
ASCII lowercase letters, digits, and hyphens for the slug, and select the required prefix from the
memory type:

- user_preference: `user-<slug>.md`
- correction_feedback: `feedback-<slug>.md`
- project_knowledge: `project-<slug>.md`
- reference: `reference-<slug>.md`

For create and update, `path` and `entry.path` must be identical. For example, a Chinese language
preference may use `user-chinese-language.md`; values such as `user_language`, `preferences.md`,
absolute paths, and paths containing directories are invalid.

For delete, omit entry. For update, preserve the existing path, name, description, and type exactly;
only body may change. Only delete paths present in the supplied current memory. Use an empty
operations array when no durable change is warranted.
