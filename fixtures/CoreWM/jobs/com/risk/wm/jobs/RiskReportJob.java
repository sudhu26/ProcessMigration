package com.risk.wm.jobs;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

/** Morning report job: reads the exposure results written overnight. */
public class RiskReportJob {

    public static void main(String[] args) throws Exception {
        Connection con = DriverManager.getConnection("jdbc:oracle:thin:@db:1521/RISK");
        try (Statement st = con.createStatement();
             ResultSet rs = st.executeQuery(
                 "SELECT BOOK, TOTAL_EXPOSURE FROM RISK_RESULTS ORDER BY BOOK")) {
            while (rs.next()) {
                System.out.println(rs.getString(1) + "=" + rs.getDouble(2));
            }
        } finally {
            con.close();
        }
    }
}
