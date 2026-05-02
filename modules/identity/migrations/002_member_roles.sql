-- Migration 002: Add first_name, last_name, role_title, role_description columns
-- and consolidate non-bootstrap roles into the generic MEMBER role.
ALTER TABLE agents
ADD COLUMN first_name TEXT NOT NULL DEFAULT '';
ALTER TABLE agents
ADD COLUMN last_name TEXT NOT NULL DEFAULT '';
ALTER TABLE agents
ADD COLUMN role_title TEXT;
ALTER TABLE agents
ADD COLUMN role_description TEXT;
-- Preserve the old specific role value as role_title for existing MEMBER agents.
UPDATE agents
SET role_title = role
WHERE role NOT IN ('ceo', 'hr', 'security');
-- Collapse all non-bootstrap roles into MEMBER.
UPDATE agents
SET role = 'member'
WHERE role NOT IN ('ceo', 'hr', 'security');