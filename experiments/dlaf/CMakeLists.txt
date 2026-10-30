# The driver copy_check.cpp against an installed DLA-Future (the report's reproduction block).
cmake_minimum_required(VERSION 3.22)
project(copy_check CXX)
find_package(DLAF REQUIRED)
add_executable(copy_check copy_check.cpp)
target_link_libraries(copy_check PRIVATE DLAF::DLAF)
