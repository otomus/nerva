//! JSON Schema validation using serde_json.
//!
//! Provides basic structural validation of JSON instances against
//! JSON Schema drafts. Validates type, required fields, and enum
//! constraints — sufficient for tool-call argument validation.

use serde_json::Value;

/// Validates a JSON instance against a JSON schema.
///
/// Returns a list of human-readable validation error messages.
/// An empty list means the instance is valid.
///
/// # Arguments
///
/// * `instance` - The JSON value to validate.
/// * `schema` - The JSON Schema to validate against.
pub fn validate(instance: &Value, schema: &Value) -> Vec<String> {
    let mut errors = Vec::new();
    validate_node(instance, schema, "", &mut errors);
    errors
}

/// Recursively validates a node against its schema definition.
fn validate_node(instance: &Value, schema: &Value, path: &str, errors: &mut Vec<String>) {
    if let Some(type_val) = schema.get("type") {
        if let Some(expected_type) = type_val.as_str() {
            if !type_matches(instance, expected_type) {
                errors.push(format!(
                    "{}: expected type '{}', got '{}'",
                    display_path(path),
                    expected_type,
                    json_type_name(instance),
                ));
                return;
            }
        }
    }

    validate_enum(instance, schema, path, errors);
    validate_required(instance, schema, path, errors);
    validate_properties(instance, schema, path, errors);
    validate_items(instance, schema, path, errors);
}

/// Checks whether the instance matches the expected JSON Schema type keyword.
fn type_matches(value: &Value, expected: &str) -> bool {
    match expected {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "number" => value.is_number(),
        "integer" => value.is_i64() || value.is_u64(),
        "boolean" => value.is_boolean(),
        "null" => value.is_null(),
        _ => true,
    }
}

/// Validates enum constraints.
fn validate_enum(instance: &Value, schema: &Value, path: &str, errors: &mut Vec<String>) {
    if let Some(enum_vals) = schema.get("enum") {
        if let Some(allowed) = enum_vals.as_array() {
            if !allowed.contains(instance) {
                errors.push(format!(
                    "{}: value not in enum {:?}",
                    display_path(path),
                    allowed,
                ));
            }
        }
    }
}

/// Validates required fields on object instances.
fn validate_required(instance: &Value, schema: &Value, path: &str, errors: &mut Vec<String>) {
    if let (Some(obj), Some(required)) = (instance.as_object(), schema.get("required")) {
        if let Some(required_arr) = required.as_array() {
            for req in required_arr {
                if let Some(field_name) = req.as_str() {
                    if !obj.contains_key(field_name) {
                        errors.push(format!(
                            "{}: missing required field '{}'",
                            display_path(path),
                            field_name,
                        ));
                    }
                }
            }
        }
    }
}

/// Validates object properties against their sub-schemas.
fn validate_properties(instance: &Value, schema: &Value, path: &str, errors: &mut Vec<String>) {
    if let (Some(obj), Some(props)) = (instance.as_object(), schema.get("properties")) {
        if let Some(props_obj) = props.as_object() {
            for (key, sub_schema) in props_obj {
                if let Some(value) = obj.get(key) {
                    let child_path = if path.is_empty() {
                        key.clone()
                    } else {
                        format!("{}.{}", path, key)
                    };
                    validate_node(value, sub_schema, &child_path, errors);
                }
            }
        }
    }
}

/// Validates array items against the items sub-schema.
fn validate_items(instance: &Value, schema: &Value, path: &str, errors: &mut Vec<String>) {
    if let (Some(arr), Some(items_schema)) = (instance.as_array(), schema.get("items")) {
        for (i, item) in arr.iter().enumerate() {
            let child_path = format!("{}[{}]", display_path(path), i);
            validate_node(item, items_schema, &child_path, errors);
        }
    }
}

/// Formats the JSON path for error messages.
fn display_path(path: &str) -> &str {
    if path.is_empty() {
        "$"
    } else {
        path
    }
}

/// Returns a human-readable type name for a JSON value.
fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn valid_object_passes() {
        let schema = json!({
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": { "type": "string" }
            }
        });
        let instance = json!({ "name": "alice" });
        assert!(validate(&instance, &schema).is_empty());
    }

    #[test]
    fn missing_required_field() {
        let schema = json!({
            "type": "object",
            "required": ["name"]
        });
        let instance = json!({});
        let errors = validate(&instance, &schema);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("missing required field 'name'"));
    }

    #[test]
    fn wrong_type_detected() {
        let schema = json!({ "type": "string" });
        let instance = json!(42);
        let errors = validate(&instance, &schema);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("expected type 'string'"));
    }

    #[test]
    fn enum_violation() {
        let schema = json!({ "type": "string", "enum": ["a", "b"] });
        let instance = json!("c");
        let errors = validate(&instance, &schema);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("not in enum"));
    }

    #[test]
    fn nested_property_validation() {
        let schema = json!({
            "type": "object",
            "properties": {
                "age": { "type": "integer" }
            }
        });
        let instance = json!({ "age": "not a number" });
        let errors = validate(&instance, &schema);
        assert_eq!(errors.len(), 1);
        assert!(errors[0].contains("age"));
    }

    #[test]
    fn array_items_validation() {
        let schema = json!({
            "type": "array",
            "items": { "type": "number" }
        });
        let instance = json!([1, "two", 3]);
        let errors = validate(&instance, &schema);
        assert_eq!(errors.len(), 1);
    }
}
