CREATE TABLE memory_working (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT,
    ts_company TEXT NOT NULL,
    ts_real TEXT NOT NULL
);
CREATE INDEX idx_working_agent_id ON memory_working(agent_id, id DESC);
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('episodic', 'semantic')),
    key TEXT,
    content TEXT NOT NULL,
    metadata_json TEXT,
    ts_real TEXT NOT NULL
);
CREATE INDEX idx_memories_agent_kind ON memories(agent_id, kind);
CREATE UNIQUE INDEX idx_memories_semantic_key ON memories(agent_id, key)
WHERE kind = 'semantic';
-- Embedding store. ``vector`` is a packed float32 BLOB; ``dim`` lets us
-- reject mixed-dimension reads after a model swap. Future: replace this
-- table with a ``sqlite-vss`` virtual table without changing service code.
CREATE TABLE memory_embeddings (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL
);
CREATE INDEX idx_memory_embeddings_agent ON memory_embeddings(agent_id);