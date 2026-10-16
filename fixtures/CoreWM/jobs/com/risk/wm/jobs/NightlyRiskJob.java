package com.risk.wm.jobs;

import com.risk.dl.DbUtil;
import java.sql.CallableStatement;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.List;

/** Nightly risk batch: loads positions, runs calc package, writes summary. */
public class NightlyRiskJob {

    public static void main(String[] args) throws Exception {
        String asof = args[0];
        NightlyRiskJob job = new NightlyRiskJob();
        job.run(asof);
    }

    public void run(String asof) throws Exception {
        Connection con = DriverManager.getConnection("jdbc:oracle:thin:@db:1521/RISK");
        try {
            con.setAutoCommit(false);

            // top-N risky books, oracle style
            String topSql = "SELECT * FROM (SELECT BOOK, SUM(EXPOSURE) E FROM RISK_POSITIONS "
                          + "WHERE AS_OF_DATE = TO_DATE('" + asof + "', 'YYYY-MM-DD') "
                          + "GROUP BY BOOK ORDER BY E DESC) WHERE ROWNUM <= 10";
            List<String> books = new ArrayList<>();
            try (Statement st = con.createStatement();
                 ResultSet rs = st.executeQuery(topSql)) {
                while (rs.next()) {
                    String b = rs.getString(1);
                    if (b != null && !b.isEmpty()) {
                        books.add(b.trim().toUpperCase());
                    } else {
                        books.add("UNKNOWN");
                    }
                }
            }

            // run the heavy calc inside the database package
            try (CallableStatement cs =
                     con.prepareCall("{call RISK_CALC_PKG.CALC_EXPOSURE(?)}")) {
                cs.setString(1, asof);
                cs.execute();
            }

            // summary read with NVL + dual check
            String chk = "SELECT NVL(MAX(TOTAL_EXPOSURE), 0) FROM RISK_RESULTS "
                       + "WHERE AS_OF_DATE = SYSDATE";
            try (Statement st2 = con.createStatement();
                 ResultSet rs2 = st2.executeQuery(chk)) {
                if (rs2.next() && rs2.getDouble(1) < 0) {
                    throw new IllegalStateException("negative exposure total");
                }
            }

            DbUtil util = new DbUtil(null);
            util.bulkInsertExposures(con, new ArrayList<>());
            con.commit();
        } catch (Exception e) {
            con.rollback();
            throw e;
        } finally {
            con.close();
        }
    }
}
