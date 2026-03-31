package registry

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	nctx "github.com/otomus/nerva/go/context"

	_ "modernc.org/sqlite"
)

const tableName = "components"

const createTableSQL = `
	CREATE TABLE IF NOT EXISTS ` + tableName + ` (
		name            TEXT PRIMARY KEY,
		kind            TEXT NOT NULL,
		description     TEXT NOT NULL,
		schema_json     TEXT,
		metadata_json   TEXT NOT NULL DEFAULT '{}',
		health          TEXT NOT NULL DEFAULT 'healthy',
		stats_json      TEXT NOT NULL DEFAULT '{}',
		enabled         INTEGER NOT NULL DEFAULT 1,
		requirements_json TEXT NOT NULL DEFAULT '[]',
		permissions_json  TEXT NOT NULL DEFAULT '[]',
		updated_at      REAL NOT NULL
	)
`

// SqliteRegistry is a registry backed by SQLite for persistence across restarts.
type SqliteRegistry struct {
	db *sql.DB
}

// NewSqliteRegistry creates a new SQLite-backed registry.
// Use ":memory:" for testing.
func NewSqliteRegistry(path string) (*SqliteRegistry, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("failed to open sqlite database: %w", err)
	}

	if _, err := db.Exec(createTableSQL); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to create table: %w", err)
	}

	return &SqliteRegistry{db: db}, nil
}

// Register adds or replaces a component in the registry.
func (r *SqliteRegistry) Register(_ *nctx.ExecContext, entry RegistryEntry) error {
	schemaJSON, _ := marshalNullable(entry.Schema)
	metadataJSON, _ := json.Marshal(entry.Metadata)
	statsJSON, _ := json.Marshal(statsToMap(entry.Stats))
	requirementsJSON, _ := json.Marshal(entry.Requirements)
	permissionsJSON, _ := json.Marshal(entry.Permissions)

	enabled := 0
	if entry.Enabled {
		enabled = 1
	}

	_, err := r.db.Exec(
		`INSERT OR REPLACE INTO `+tableName+`
			(name, kind, description, schema_json, metadata_json,
			 health, stats_json, enabled, requirements_json,
			 permissions_json, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		entry.Name, string(entry.Kind), entry.Description,
		schemaJSON, string(metadataJSON),
		string(entry.Health), string(statsJSON), enabled,
		string(requirementsJSON), string(permissionsJSON),
		float64(time.Now().UnixMilli())/1000.0,
	)
	return err
}

// Discover lists components of a given kind visible to the caller.
func (r *SqliteRegistry) Discover(ctx *nctx.ExecContext, kind ComponentKind) ([]RegistryEntry, error) {
	rows, err := r.db.Query(
		`SELECT name, kind, description, schema_json, metadata_json,
			health, stats_json, enabled, requirements_json, permissions_json
		 FROM `+tableName+`
		 WHERE kind = ? AND enabled = 1 AND health != ?
		 ORDER BY name`,
		string(kind), string(HealthUnavailable),
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []RegistryEntry
	for rows.Next() {
		entry, err := scanEntry(rows)
		if err != nil {
			return nil, err
		}
		if len(entry.Permissions) > 0 && !hasRequiredPermission(&entry, ctx) {
			continue
		}
		results = append(results, entry)
	}

	return results, rows.Err()
}

// Resolve looks up a single component by name.
func (r *SqliteRegistry) Resolve(_ *nctx.ExecContext, name string) (*RegistryEntry, error) {
	row := r.db.QueryRow(
		`SELECT name, kind, description, schema_json, metadata_json,
			health, stats_json, enabled, requirements_json, permissions_json
		 FROM `+tableName+` WHERE name = ?`, name,
	)

	entry, err := scanEntryRow(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &entry, nil
}

// Health gets the current health status of a component.
func (r *SqliteRegistry) Health(name string) (HealthStatus, error) {
	var health string
	err := r.db.QueryRow(
		`SELECT health FROM `+tableName+` WHERE name = ?`, name,
	).Scan(&health)
	if err == sql.ErrNoRows {
		return "", fmt.Errorf("component not found: %q", name)
	}
	if err != nil {
		return "", err
	}
	return HealthStatus(health), nil
}

// Update applies a partial update to a registered component.
func (r *SqliteRegistry) Update(name string, patch RegistryPatch) error {
	entry, err := r.Resolve(nctx.NewContext(), name)
	if err != nil {
		return err
	}
	if entry == nil {
		return fmt.Errorf("component not found: %q", name)
	}

	applyPatch(entry, patch)

	return r.Register(nctx.NewContext(), *entry)
}

// Close closes the database connection.
func (r *SqliteRegistry) Close() error {
	return r.db.Close()
}

func scanEntry(rows *sql.Rows) (RegistryEntry, error) {
	var (
		name, kind, description, health string
		schemaJSON, metadataJSON        sql.NullString
		statsJSON, requirementsJSON     string
		permissionsJSON                 string
		enabled                         int
	)

	err := rows.Scan(&name, &kind, &description, &schemaJSON, &metadataJSON,
		&health, &statsJSON, &enabled, &requirementsJSON, &permissionsJSON)
	if err != nil {
		return RegistryEntry{}, err
	}

	return buildEntry(name, kind, description, schemaJSON, metadataJSON,
		health, statsJSON, enabled, requirementsJSON, permissionsJSON)
}

func scanEntryRow(row *sql.Row) (RegistryEntry, error) {
	var (
		name, kind, description, health string
		schemaJSON, metadataJSON        sql.NullString
		statsJSON, requirementsJSON     string
		permissionsJSON                 string
		enabled                         int
	)

	err := row.Scan(&name, &kind, &description, &schemaJSON, &metadataJSON,
		&health, &statsJSON, &enabled, &requirementsJSON, &permissionsJSON)
	if err != nil {
		return RegistryEntry{}, err
	}

	return buildEntry(name, kind, description, schemaJSON, metadataJSON,
		health, statsJSON, enabled, requirementsJSON, permissionsJSON)
}

func buildEntry(
	name, kind, description string,
	schemaJSON, metadataJSON sql.NullString,
	health, statsJSON string,
	enabled int,
	requirementsJSON, permissionsJSON string,
) (RegistryEntry, error) {
	var schema map[string]any
	if schemaJSON.Valid && schemaJSON.String != "" {
		json.Unmarshal([]byte(schemaJSON.String), &schema)
	}

	metadata := make(map[string]string)
	if metadataJSON.Valid {
		json.Unmarshal([]byte(metadataJSON.String), &metadata)
	}

	stats := statsFromJSON(statsJSON)

	var requirements []string
	json.Unmarshal([]byte(requirementsJSON), &requirements)

	var permissions []string
	json.Unmarshal([]byte(permissionsJSON), &permissions)

	return RegistryEntry{
		Name:         name,
		Kind:         ComponentKind(kind),
		Description:  description,
		Schema:       schema,
		Metadata:     metadata,
		Health:       HealthStatus(health),
		Stats:        stats,
		Enabled:      enabled != 0,
		Requirements: requirements,
		Permissions:  permissions,
	}, nil
}

func statsToMap(s InvocationStats) map[string]any {
	m := map[string]any{
		"total_calls":     s.TotalCalls,
		"successes":       s.Successes,
		"failures":        s.Failures,
		"avg_duration_ms": s.AvgDurationMs,
	}
	if s.LastInvokedAt != nil {
		m["last_invoked_at"] = s.LastInvokedAt.Unix()
	}
	return m
}

func statsFromJSON(raw string) InvocationStats {
	var data map[string]any
	if err := json.Unmarshal([]byte(raw), &data); err != nil {
		return InvocationStats{}
	}

	s := InvocationStats{}
	if v, ok := data["total_calls"].(float64); ok {
		s.TotalCalls = int(v)
	}
	if v, ok := data["successes"].(float64); ok {
		s.Successes = int(v)
	}
	if v, ok := data["failures"].(float64); ok {
		s.Failures = int(v)
	}
	if v, ok := data["avg_duration_ms"].(float64); ok {
		s.AvgDurationMs = v
	}
	if v, ok := data["last_invoked_at"].(float64); ok {
		t := time.Unix(int64(v), 0)
		s.LastInvokedAt = &t
	}
	return s
}

func marshalNullable(v map[string]any) (sql.NullString, error) {
	if v == nil {
		return sql.NullString{Valid: false}, nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		return sql.NullString{}, err
	}
	return sql.NullString{String: string(b), Valid: true}, nil
}
