"""
Unit tests for JSON schema validation
"""

import pytest
import json
from src.utils.schema_validator import (
    validate_schema_structure,
    validate_and_parse_schema,
    SchemaValidationError
)


@pytest.mark.unit
class TestSchemaValidation:
    """Test JSON schema validation"""

    def test_valid_schema_passes(self):
        """Test valid schema structure passes validation"""
        valid_schema = {
            "PropertyInfo": [
                {"Property_Address": None, "ListPrice": None}
            ],
            "CustomerInfo": [
                {"Customer_Name": None, "Customer_Email": None}
            ]
        }
        validate_schema_structure(valid_schema)  # Should not raise

    def test_empty_schema_fails(self):
        """Test empty schema raises error"""
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure({})
        assert "cannot be empty" in str(exc.value)

    def test_non_dict_schema_fails(self):
        """Test non-dictionary schema raises error"""
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(["not", "a", "dict"])
        assert "must be a dictionary" in str(exc.value)

    def test_table_with_non_list_fails(self):
        """Test table mapping to non-list fails"""
        invalid_schema = {
            "PropertyInfo": {"not": "a list"}
        }
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(invalid_schema)
        assert "must map to a list" in str(exc.value)

    def test_table_with_empty_list_fails(self):
        """Test table with empty list fails"""
        invalid_schema = {
            "PropertyInfo": []
        }
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(invalid_schema)
        assert "at least one template row" in str(exc.value)

    def test_table_with_non_dict_template_fails(self):
        """Test table with non-dict template fails"""
        invalid_schema = {
            "PropertyInfo": ["not a dict"]
        }
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(invalid_schema)
        assert "template must be a dict" in str(exc.value)

    def test_too_many_tables_fails(self):
        """Test schema with too many tables fails"""
        huge_schema = {f"Table{i}": [{"field": None}] for i in range(100)}
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(huge_schema)
        assert "too many tables" in str(exc.value)

    def test_too_many_fields_fails(self):
        """Test table with too many fields fails"""
        template = {f"field{i}": None for i in range(300)}
        invalid_schema = {"PropertyInfo": [template]}
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(invalid_schema)
        assert "too many fields" in str(exc.value)

    def test_excessive_nesting_fails(self):
        """Test excessive nesting depth fails"""
        deeply_nested = {
            "Table1": [
                {
                    "level1": {
                        "level2": {
                            "level3": {
                                "level4": "too deep"
                            }
                        }
                    }
                }
            ]
        }
        with pytest.raises(SchemaValidationError) as exc:
            validate_schema_structure(deeply_nested)
        assert "nesting too deep" in str(exc.value)


@pytest.mark.unit
class TestSchemaParsingAndValidation:
    """Test schema parsing with validation"""

    def test_valid_json_schema_parses(self):
        """Test valid JSON schema parses successfully"""
        schema_json = '{"PropertyInfo": [{"Address": null, "Price": null}]}'
        schema_bytes = schema_json.encode('utf-8')

        result = validate_and_parse_schema(schema_bytes)
        assert isinstance(result, dict)
        assert "PropertyInfo" in result

    def test_invalid_json_fails(self):
        """Test invalid JSON raises SchemaValidationError"""
        invalid_json = b'{invalid json syntax}'

        with pytest.raises(SchemaValidationError) as exc:
            validate_and_parse_schema(invalid_json)
        assert "Invalid JSON" in str(exc.value)

    def test_schema_size_limit_enforced(self):
        """Test schema size limit is enforced"""
        # Create a schema that's too large (> 1MB)
        huge_schema = {"Field" + str(i): [{"key": "x" * 10000}] for i in range(200)}
        huge_json = json.dumps(huge_schema).encode('utf-8')

        with pytest.raises(SchemaValidationError) as exc:
            validate_and_parse_schema(huge_json)
        assert "too large" in str(exc.value)

    def test_non_utf8_encoding_fails(self):
        """Test non-UTF8 encoding raises error"""
        invalid_encoding = b'\x80\x81\x82\x83'  # Invalid UTF-8

        with pytest.raises(SchemaValidationError) as exc:
            validate_and_parse_schema(invalid_encoding)
        assert "UTF-8 encoding" in str(exc.value)
