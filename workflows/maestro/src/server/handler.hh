#ifndef HTTP_HANDLER_HH
#define HTTP_HANDLER_HH

#include "http.hh"

#include <map>
#include <stdexcept>
#include <string>

namespace HTTP {
  class InvalidJsonMaps : public std::invalid_argument {
  public:
    using std::invalid_argument::invalid_argument;
  };

  class Handler {
  public:
    Handler() = default;
    // Sub-classes may potentially be used via pointers to base so virtual destructor
    virtual ~Handler() = default;

    virtual Response operator()(const HTTP::Request& request) = 0;
  };

  class JsonHandler : public Handler {
  public:
    JsonHandler() = default;
    virtual ~JsonHandler() = default;

    virtual Response operator()(const HTTP::Request& request) = 0;

  protected:
    void parse_json(const std::string& json_string,
                    std::map<std::string, std::string>& result);

    std::string to_json_str(const std::map<std::string, std::string>& json_map);
    std::string to_json_str(const std::map<std::string, std::string>& keys,
                            const std::map<std::string, std::string>& vals);

  private:
    std::string trim(const std::string& str) const;
  };
} // namespace HTTP

#endif
