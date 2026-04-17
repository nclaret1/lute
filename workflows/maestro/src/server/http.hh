#ifndef HTTP_HH
#define HTTP_HH

#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

namespace HTTP {
  enum class METHOD {
    GET,
    HEAD,
    POST,
    PUT,
    DELETE,
    CONNECT,
    OPTIONS,
    TRACE,
    PATCH
  };

  enum class VERSION {
    H_0_9 = 9,
    H_1_0 = 10,
    H_1_1 = 11,
    H_2_0 = 20
  };

  enum class CODE {
    OK = 200,
    BadRequest = 400,
    Unauthorized = 401,
    Forbidden = 403,
    NotFound = 404,
    MethodNotAllowed = 405,
    InternalServerError = 500
  };

  class IncompleteHeader : public std::invalid_argument {
  public:
    using std::invalid_argument::invalid_argument;
  };

  class Interface {
  public:
    Interface()
        : m_version(VERSION::H_1_1)
        , m_persistent(true)
    {}
    virtual ~Interface() = default;

    void set_header(const std::string& key, const std::string& value) {
      m_headers[key] = value;
    }

    void set_content(const std::string& content) {
      m_content = std::move(content);
      set_header("Content-Length", std::to_string(m_content.length()));
    }
    std::string content() const {
      return m_content;
    }

    size_t content_length() const {
      return m_content.length();
    }

    std::map<std::string, std::string> headers() const {
      return m_headers;
    }

    VERSION version() const { return m_version; }

    /**
     * Whether we are using a persistent (keep-alive) connection. This is the
     * default for HTTP/1.1 and forward, and `Connection: close` must be added
     * as a header if not.
     */
    bool persistent() const { return m_persistent; }

  protected:
    VERSION m_version;
    std::map<std::string, std::string> m_headers;
    std::string m_content;
    bool m_persistent;
  };

  class Request : public Interface {
  public:
    Request()
        : m_method(METHOD::POST)
    {}

    Request(METHOD method)
        : m_method(method)
    {}

    Request(const std::string& raw_http_request)
        : Interface()
    {
      parse_from_string(raw_http_request);
    }
    void set_url(const std::string& url) { m_url = url; }

    void set_method(METHOD method) { m_method = method; }
    ~Request() = default;

    METHOD method() const { return m_method; }

    std::string url() const { return m_url; }

  private:
    METHOD m_method;
    std::string m_url;

    /**
     * Parse out the method, route and version from the start line of a request
     *
     * @param raw_http The HTTP request string.
     *
     * @return header_start The position in the string that the headers begin.
     *         This is after the `\r\n` of the first start line.
     * @throw IncompleteHeader if `\r\n` is missing.
     * @throw invalid_argument if it cannot parse out the information.
     */
    size_t parse_start_line(const std::string_view raw_http);

    /**
     * Parse out the headers.
     *
     * @param raw_http The HTTP request string.
     * @param headers_start The position in the string that the headers start.
     *
     * @return body_start The position in the string that the body begins. If any.
     *         This is after the `\r\n\r\n` marking the end of headers.
     * @throw IncompleteHeader if `\r\n\r\n` is missing.
     * @throw invalid_argument if it cannot parse out the information.
     */
    size_t parse_headers(const std::string_view raw_http, size_t& headers_start);

    /**
     * Parse the message body.
     * This function expects to receive a substring from the full request that
     * already has the start line and headers removed.
     *
     * @param raw_http The HTTP request string.
     * @param body_start The position in the string that the body begins. If
     *        any. This is after the `\r\n\r\n` marking the end of headers.
     */
    void parse_body(const std::string_view raw_http, size_t& body_start);

    /**
     * Parse the entire request string.
     */
    void parse_from_string(const std::string& raw_http_request);
  };
  class Response : public Interface {
  public:
    Response()
        : m_status_code(CODE::OK)
    {}

    Response(CODE status_code)
        : m_status_code(status_code)
    {}

    CODE status_code() const { return m_status_code; }
    ~Response() = default;

    /**
     * Return the status (first) line of the response. It has the format:
     * <VERSION> SPACE <CODE> SPACE <REASON> CRLF
     */
    std::string status_line() const;

    /**
     * Return the entire HTTP response as a string.
     */
    std::string to_string() const;

  private:
    CODE m_status_code;
  };

  VERSION string_to_version(const std::string& version_string);
  std::string version_to_string(const VERSION& version);
  std::string method_to_string(const METHOD& method);
  METHOD string_to_method(const std::string& method_str);
  std::string code_to_string(const CODE& code);
} // Namespace HTTP
#endif
