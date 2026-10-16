package com.risk.dl;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import javax.sql.DataSource;

/** Shared DB helper used by CoreWM jobs (Risk_DL common code). */
public class DbUtil {
    private final DataSource ds;

    public DbUtil(DataSource ds) {
        this.ds = ds;
    }

    public int bulkInsertExposures(Connection con, java.util.List<Object[]> rows)
            throws SQLException {
        String sql = "INSERT INTO RISK_POSITIONS(POSITION_ID, BOOK, EXPOSURE, AS_OF_DATE)"
                   + " VALUES (?, ?, ?, ?)";
        int total = 0;
        try (PreparedStatement ps = con.prepareStatement(sql)) {
            for (Object[] r : rows) {
                ps.setObject(1, r[0]);
                ps.setObject(2, r[1]);
                ps.setObject(3, r[2]);
                ps.setObject(4, r[3]);
                ps.addBatch();
            }
            for (int n : ps.executeBatch()) {
                total += n;
            }
        }
        con.commit();
        return total;
    }
}
