#include "handler.hh"

#include "rapidjson/document.h"
#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"

#include <string>

namespace HTTP {
  std::string JsonHandler::trim(const std::string& str) const {
    size_t first = str.find_first_not_of(" \t\r\n");
    size_t last = str.find_last_not_of(" \t\r\n");
    return (first == std::string::npos || last == std::string::npos)
      ? ""
      : str.substr(first, last - first + 1);
  }

  void JsonHandler::parse_json(const std::string& json_string,
                               std::map<std::string, std::string>& result) {

    rapidjson::Document json;
    json.Parse(json_string.c_str());
    if (json.HasParseError()) {
      result["JSON_PARSE_ERROR"] = "true";
      return;
    }
    for (auto member = json.MemberBegin(); member != json.MemberEnd(); ++member) {
      std::string key {member->name.GetString()};
      std::string val;
      // We don't do multiple layers at the moment. If its not an actual string
      // we'll just put it back in as a string
      if (member->value.IsString()) {
        val = member->value.GetString();
      } else {
        // Serialize it back into a string
        rapidjson::StringBuffer buffer;
        rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
        member->value.Accept(writer);
        val = buffer.GetString();
      }
      result[key] = val;
    }
  }

  /**
   * Convert a map directly into a JSON string.
   * Every key/value pair will be added directly to the JSON string.
   *
   * @param[in] json_map The map to JSON "stringify".
   * @returns JSON string representation of the map.
   */
  std::string JsonHandler::to_json_str(const std::map<std::string, std::string>& json_map) {
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);

    writer.StartObject();

    for (const auto& [key, val] : json_map) {
      bool success = writer.Key(key.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on key " + key);
      }
      success = writer.String(val.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on value " + val);
      }
    }

    writer.EndObject();

    return buffer.GetString();
  }

  /**
   * Convert a set of maps into a JSON string.
   * This is conceptually like `boost:combine` or various implementations of `zip`
   * applied to maps.
   *
   * In this case, the value map (`vals`) may have had the keys removed, so a new
   * map `keys` can be provided.
   * E.g. keys may look like: {"val0_name": "val1_name"}
   *  and vals may look like: {"val0": "val1"}
   * The resultant string will give you:
   * {"val0_name": "val0", "val1_name": "val1"}
   *
   * @param[in] keys The key map to JSON "stringify".
   * @param[in] vals The value map to JSON "stringify".
   * @returns JSON string representation of the map.
   */
  std::string JsonHandler::to_json_str(const std::map<std::string, std::string>& keys,
                                       const std::map<std::string, std::string>& vals) {
    rapidjson::StringBuffer buffer;
    rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);

    if (keys.size() != vals.size()) {
      throw InvalidJsonMaps("Length of keys and does not match length of vals!");
    }
    writer.StartObject();

    const auto key_end = keys.end();
    const auto val_end = vals.end();

    auto key_it = keys.begin();
    auto val_it = vals.begin();
    for (; key_it != key_end && val_it != val_end; ++key_it, ++val_it) {
      const auto& [key1, key2] = *key_it;
      const auto& [val1, val2] = *val_it;
      bool success = writer.Key(key1.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on key " + key1);
      }
      success = writer.String(val1.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on value " + val1);
      }

      success = writer.Key(key2.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on key " + key2);
      }
      success = writer.String(val2.c_str());
      if (!success) {
        throw InvalidJsonMaps("Error creating JSON string on value " + val2);
      }
    }

    writer.EndObject();

    return buffer.GetString();
  }
} // namespace HTTP
