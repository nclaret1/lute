#include "http.hh"

#include <algorithm>
#include <cctype>
#include <iterator>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

namespace HTTP {
  VERSION string_to_version(const std::string& version_string) {
    std::string version_string_uppercase;
    std::transform(version_string.begin(), version_string.end(),
                   std::back_inserter(version_string_uppercase),
                   [](char c) { return toupper(c); });
    if (version_string_uppercase == "HTTP/0.9") {
      return VERSION::H_0_9;
    } else if (version_string_uppercase == "HTTP/1.0") {
      return VERSION::H_1_0;
    } else if (version_string_uppercase == "HTTP/1.1") {
      return VERSION::H_1_1;
    } else if (version_string_uppercase == "HTTP/2" ||
               version_string_uppercase == "HTTP/2.0") {
      return VERSION::H_2_0;
    } else {
      throw std::invalid_argument("Unexpected HTTP version");
    }
  }
  std::string version_to_string(const VERSION& version) {
    switch (version) {
    case VERSION::H_0_9:
      return std::string("HTTP/0.9");
    case VERSION::H_1_0:
      return std::string("HTTP/1.0");
    case VERSION::H_1_1:
      return std::string("HTTP/1.1");
    case VERSION::H_2_0:
      return std::string("HTTP/2.0");
    default:
      throw std::invalid_argument("Unrecognized HTTP version supplied.");
    }
  }

  std::string method_to_string(const METHOD& method) {
    switch(method) {
    case METHOD::GET:
      return std::string("GET");
    case METHOD::HEAD:
      return std::string("HEAD");
    case METHOD::POST:
      return std::string("POST");
    case METHOD::PUT:
      return std::string("PUT");
    case METHOD::DELETE:
      return std::string("DELETE");
    case METHOD::CONNECT:
      return std::string("CONNECT");
    case METHOD::OPTIONS:
      return std::string("OPTIONS");
    case METHOD::TRACE:
      return std::string("TRACE");
    case METHOD::PATCH:
      return std::string("PATCH");
    default:
      throw std::invalid_argument("Unrecognized HTTP method supplied.");
    }
  }

  METHOD string_to_method(const std::string& method_str) {
    if (method_str == "GET") {
      return METHOD::GET;
    } else if (method_str == "HEAD") {
      return METHOD::HEAD;
    } else if (method_str == "POST") {
      return METHOD::POST;
    } else if (method_str == "PUT") {
      return METHOD::PUT;
    } else if (method_str == "DELETE") {
      return METHOD::DELETE;
    } else if (method_str == "CONNECT") {
      return METHOD::CONNECT;
    } else if (method_str == "OPTIONS") {
      return METHOD::OPTIONS;
    } else if (method_str == "TRACE") {
      return METHOD::TRACE;
    } else if (method_str == "PATCH") {
      return METHOD::PATCH;
    } else {
      throw std::invalid_argument("Unrecognized HTTP method supplied.");
    }
  }

  std::string code_to_string(const CODE& code) {
    switch(code) {
    case CODE::OK:
      return std::string("200 OK");
    case CODE::BadRequest:
      return std::string("400 BadRequest");
    case CODE::Unauthorized:
      return std::string("401 Unauthorized");
    case CODE::Forbidden:
      return std::string("403 Forbidden");
    case CODE::NotFound:
      return std::string("404 NotFound");
    case CODE::MethodNotAllowed:
      return std::string("405 MethodNotAllowed");
    case CODE::InternalServerError:
      return std::string("500 InternalServerError");
    default:
      throw std::invalid_argument("Unrecognized, or unsupported status code supplied.");
    }
  }

  size_t Request::parse_start_line(const std::string_view raw_http) {
    size_t first_crlf = raw_http.find("\r\n", 0);
    if (first_crlf == std::string::npos) {
      throw IncompleteHeader("Could not find request start line. Missing CRLF?");
    }

    std::string_view start_line = raw_http.substr(0, first_crlf);

    // Format is <METHOD> <ROUTE> <VERSION>
    size_t method_end = start_line.find(' ');
    size_t route_end = start_line.find(' ', method_end + 1);
    if (method_end == std::string_view::npos || route_end == std::string_view::npos) {
      throw std::invalid_argument("Invalid start line (no spaces): " + std::string(start_line));
    }

    std::string_view method = start_line.substr(0, method_end);
    std::string_view route = start_line.substr(method_end + 1, route_end - method_end - 1);
    std::string_view version = start_line.substr(route_end + 1);

    m_method = string_to_method(std::string(method));
    m_url = std::string(route);
    m_version = string_to_version(std::string(version));

    return first_crlf + 2;
  }

  size_t Request::parse_headers(const std::string_view raw_http, size_t& headers_start) {
    size_t headers_end = raw_http.find("\r\n\r\n", headers_start);

    if (headers_end == std::string_view::npos) {
      throw IncompleteHeader("Malformed HTTP headers (missing CRLFCRLF)");
    }

    std::string_view headers = raw_http.substr(headers_start, headers_end - headers_start);

    size_t line_start = 0;

    while (line_start < headers.size()) {
      size_t line_end = headers.find("\r\n", line_start);
      if (line_end == std::string_view::npos) {
        line_end = headers.size();
      }

      std::string_view line = headers.substr(line_start, line_end - line_start);
      if(line.empty()) {
        break;
      }

      size_t colon_pos = line.find(':');
      if (colon_pos != std::string_view::npos) {
        std::string_view key = line.substr(0, colon_pos);
        std::string_view value = line.substr(colon_pos + 1);

        auto trim = [](std::string_view sv) -> std::string_view {
          size_t start = 0;
          while (start < sv.size() && std::isspace(static_cast<unsigned char>(sv[start]))) {
            ++start;
          }
          size_t end = sv.size();
          while (end > start && std::isspace(static_cast<unsigned char>(sv[end - 1]))) {
            --end;
          }
          return sv.substr(start, end - start);
        };

        key = trim(key);
        value = trim(value);

        if (!key.empty()) {
          std::string key_str(key);
          std::string val_str(value);
          set_header(key_str, val_str);

          if (key_str == "Connection" && val_str == "close") {
            m_persistent = false;
          }
        }
      }

      line_start = line_end + 2;
    }
    return headers_end + 4;
  }

  void Request::parse_body(const std::string_view raw_http, size_t& body_start) {
    size_t len = raw_http.length();
    if (body_start < len) {
      m_content = raw_http.substr(body_start, len - body_start);
    }
  }

  void Request::parse_from_string(const std::string& raw_http) {
    size_t headers_start = parse_start_line(raw_http);
    size_t body_start = parse_headers(raw_http, headers_start);

    parse_body(raw_http, body_start);
  }

  std::string Response::status_line() const {
    // <VERSION> SPACE <CODE> SPACE <REASON> CRLF
    // The code_to_string function returns both the code and the reason.
    std::string status_line(version_to_string(m_version) + " " +
                            code_to_string(m_status_code) + "\r\n");
    return status_line;
  }

  std::string Response::to_string() const {
    std::string response_string(status_line());
    for (auto& [header_key, header_value] : m_headers) {
      response_string += header_key + ": " + header_value + "\r\n";
    }
    // Need an extra \r\n after headers
    response_string += "\r\n";
    if (!m_content.empty()) {
      response_string += m_content;
    }
    return response_string;
  }
} // namespace HTTP
