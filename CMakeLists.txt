cmake_minimum_required(VERSION 3.22)
project(speedtest VERSION 0.1.0 LANGUAGES CXX)

# The active project is C++ only. Historical implementations are never added
# to the build; their explicitly invoked tools live under archive/.
include(CTest)
add_subdirectory(cpp)
