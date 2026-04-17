#include "http.hh"

#include <gtest/gtest.h>

using namespace HTTP;

TEST(HTTPTest, ParseSimpleRequest) {
    std::string raw = "POST /status HTTP/1.1\r\n"
                      "Content-Length: 13\r\n"
                      "Connection: close\r\n"
                      "\r\n"
                      "Hello World!!";

    Request req(raw);
    EXPECT_EQ(req.method(), METHOD::POST);
    EXPECT_EQ(req.url(), "/status");
    EXPECT_EQ(req.version(), VERSION::H_1_1);
    EXPECT_EQ(req.content(), "Hello World!!");
    EXPECT_FALSE(req.persistent());
}

TEST(HTTPTest, ParseHeaders) {
    std::string raw = "GET /index.html HTTP/1.1\r\n"
                      "Host: localhost\r\n"
                      "User-Agent: test\r\n"
                      "\r\n";
    Request req(raw);
    auto headers = req.headers();
    EXPECT_EQ(headers["Host"], "localhost");
    EXPECT_EQ(headers["User-Agent"], "test");
}

TEST(HTTPTest, ResponseToString) {
    Response res(CODE::OK);
    res.set_content("Success");
    res.set_header("X-Test", "value");

    std::string s = res.to_string();
    EXPECT_NE(s.find("HTTP/1.1 200 OK"), std::string::npos);
    EXPECT_NE(s.find("Content-Length: 7"), std::string::npos);
    EXPECT_NE(s.find("X-Test: value"), std::string::npos);
    EXPECT_NE(s.find("\r\n\r\nSuccess"), std::string::npos);
}

TEST(HTTPTest, MethodToString) {
    EXPECT_EQ(method_to_string(METHOD::GET), "GET");
    EXPECT_EQ(method_to_string(METHOD::POST), "POST");
}

TEST(HTTPTest, CodeToString) {
    EXPECT_EQ(code_to_string(CODE::OK), "200 OK");
    EXPECT_EQ(code_to_string(CODE::NotFound), "404 NotFound");
}
